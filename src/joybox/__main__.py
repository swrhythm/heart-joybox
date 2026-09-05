"""Command line entry point: ``joybox <command>``."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

from . import __version__, app, config as config_module, health, selftest
from .cache import RenderCache
from .content import scan
from .printer import PrintBlocked, ReceiptPrinter
from .raster import RenderOptions, preview, render
from .transport import CharDeviceTransport, FileTransport, MemoryTransport, TransportError

log = logging.getLogger("joybox")


def _keep_output_printable() -> None:
    """Never let a filename stop a diagnostic from printing.

    Names come off a FAT32 card, so they are whatever bytes a Windows or Mac
    laptop wrote; vfat hands them over as latin-1 and Python escapes the
    undecodable ones into surrogates.  Under a regional UTF-8 locale - which is
    what an SSH login gets - printing one of those raises UnicodeEncodeError,
    turning ``joybox list`` and ``joybox doctor``, the two commands the
    troubleshooting guide leans on, into a traceback at the worst moment.

    backslashreplace rather than replace, so the output still says which byte
    was odd and the file can be found and renamed.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pytest's capture, or a plain pipe wrapper
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):  # pragma: no cover - unusual streams
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="joybox",
        description="One-button Bible verse thermal printer.",
    )
    parser.add_argument("--version", action="version", version=f"joybox {__version__}")
    parser.add_argument("--config", type=Path, help="extra config file, read last")
    parser.add_argument("--content", type=Path, help="override the content folder")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug detail")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="run the kiosk service (what systemd starts)")

    printer_args = argparse.ArgumentParser(add_help=False)
    printer_args.add_argument("--dry-run", action="store_true",
                              help="build the job but do not send it to the printer")
    printer_args.add_argument("--output", type=Path, metavar="FILE",
                              help="write the raw ESC/POS job to a file instead of printing")

    one = sub.add_parser("print", parents=[printer_args],
                         help="print one receipt now, exactly as the button would")
    one.add_argument("--image", type=Path, nargs="+", metavar="PATH",
                     help="print these images instead of header/random body/footer")

    sub.add_parser("test", parents=[printer_args], help="print the self-test page")
    sub.add_parser("diagnostics", parents=[printer_args],
                   help="print the status slip (same as holding the button)")

    doctor = sub.add_parser("doctor", help="check everything a button press needs")
    doctor.add_argument("--quiet", action="store_true", help="only show problems")

    sub.add_parser("status", help="ask the printer how it is doing")
    sub.add_parser("list", help="show the images that would be printed")

    render_cmd = sub.add_parser("render", help="render an image the way the printer will")
    render_cmd.add_argument("image", type=Path)
    render_cmd.add_argument("--preview", type=Path, metavar="OUT.png",
                            help="save a picture of what the paper will look like")
    render_cmd.add_argument("--output", type=Path, metavar="OUT.bin",
                            help="save the raw ESC/POS bytes")
    render_cmd.add_argument("--width", type=int, help="override print width in dots")
    render_cmd.add_argument("--dither", action="store_true", help="dither instead of threshold")

    feed = sub.add_parser("feed", parents=[printer_args], help="feed blank paper")
    feed.add_argument("lines", type=int, nargs="?", default=4)
    sub.add_parser("cut", parents=[printer_args], help="feed and cut the paper")
    return parser


def load_config(args: argparse.Namespace) -> config_module.Config:
    settings = config_module.load(args.config)
    if args.content:
        settings = dataclasses.replace(
            settings, content=dataclasses.replace(settings.content, dir=args.content)
        )
    return settings


def open_transport(settings: config_module.Config, args: argparse.Namespace):
    if getattr(args, "output", None):
        return FileTransport(args.output)
    if getattr(args, "dry_run", False):
        return MemoryTransport()
    return CharDeviceTransport(
        settings.printer.device,
        settings.printer.fallback_devices,
        chunk_bytes=settings.printer.write_chunk_bytes,
        pause_seconds=settings.printer.write_pause_seconds,
        write_timeout_seconds=settings.printer.write_timeout_seconds,
        status_timeout_seconds=settings.printer.status_timeout_seconds,
    )


def make_printer(settings: config_module.Config, args: argparse.Namespace) -> ReceiptPrinter:
    cache = RenderCache(app.render_options(settings))
    return ReceiptPrinter(settings, open_transport(settings, args), cache)


def scan_content(settings: config_module.Config):
    return scan(settings.content.dir, settings.content.header,
                settings.content.footer, settings.content.body_dir)


# ------------------------------------------------------------------ commands

def cmd_print(settings, args) -> int:
    printer = make_printer(settings, args)
    try:
        if args.image:
            result = printer.print_images(list(args.image))
        else:
            content = scan_content(settings)
            if not content.ready:
                print(f"nothing to print: no images in "
                      f"{settings.content.dir / settings.content.body_dir}", file=sys.stderr)
                for problem in content.problems:
                    print(f"  - {problem}", file=sys.stderr)
                return 1
            result = printer.print_receipt(content)
    except PrintBlocked as exc:
        print(f"printer not ready: {exc}", file=sys.stderr)
        return 1
    except (TransportError, RuntimeError, OSError) as exc:
        print(f"print failed: {exc}", file=sys.stderr)
        return 1
    finally:
        printer.transport.close()

    where = "written" if getattr(args, "output", None) else "printed"
    print(f"{where}: {', '.join(result.sections)} ({result.bytes_sent} bytes, {result.seconds:.2f}s)")
    for name in result.skipped:
        print(f"  ! skipped {name}: could not be rendered", file=sys.stderr)
    return 0


def cmd_test(settings, args) -> int:
    printer = make_printer(settings, args)
    page = selftest.build(settings.printing.width_dots)
    job = render(page, app.render_options(settings))
    try:
        printer.transport.write(b"\x1b@" + job.data + printer.trailer())
    except (TransportError, OSError) as exc:
        print(f"self test failed: {exc}", file=sys.stderr)
        return 1
    finally:
        printer.transport.close()
    print(f"self-test page sent ({len(job.data)} bytes, {job.width}x{job.height} dots)")
    return 0


def cmd_diagnostics(settings, args) -> int:
    printer = make_printer(settings, args)
    content = scan_content(settings)
    try:
        lines = health.diagnostics_lines(settings, content, printer.status().describe())
        printer.print_lines(lines, title="heart joybox status")
    except (TransportError, OSError) as exc:
        print(f"could not print diagnostics: {exc}", file=sys.stderr)
        return 1
    finally:
        printer.transport.close()
    print("diagnostics slip sent")
    return 0


def cmd_doctor(settings, args) -> int:
    checks = health.run_checks(settings)
    failed = 0
    for check in checks:
        if args.quiet and check.status == health.PASS:
            continue
        marker = {health.PASS: "ok  ", health.WARN: "warn", health.FAIL: "FAIL"}[check.status]
        print(f"[{marker}] {check.name:<16} {check.detail}")
        failed += check.failed
    print()
    if failed:
        print(f"{failed} check(s) failed - see docs/TROUBLESHOOTING.md")
        return 2
    print("all checks passed - press the button")
    return 0


def cmd_status(settings, args) -> int:
    transport = CharDeviceTransport(
        settings.printer.device, settings.printer.fallback_devices,
        status_timeout_seconds=settings.printer.status_timeout_seconds,
    )
    try:
        status = transport.status()
    finally:
        transport.close()
    print(f"device: {transport.describe()}")
    print(f"state:  {status.describe()}")
    if status.raw is not None:
        print(f"raw:    0x{status.raw:02x} (from {status.source})")
    return 1 if status.blocked else 0


def cmd_list(settings, args) -> int:
    content = scan_content(settings)
    print(f"content folder: {content.directory}")
    print(f"  header: {content.header.name if content.header else '(none)'}")
    print(f"  footer: {content.footer.name if content.footer else '(none)'}")
    print(f"  body:   {len(content.bodies)} image(s)")
    for path in content.bodies:
        print(f"    - {path.name}")
    for problem in content.problems:
        print(f"  ! {problem}")
    return 0 if content.ready else 1


def cmd_render(settings, args) -> int:
    options = RenderOptions(
        width_dots=args.width or settings.printing.width_dots,
        dither=args.dither or settings.printing.dither,
        threshold=settings.printing.threshold,
        max_height=settings.printing.max_image_height,
        band_height=settings.printing.band_height,
    )
    try:
        bitmap = preview(args.image, options)
    except (OSError, ValueError) as exc:
        print(f"cannot render {args.image}: {exc}", file=sys.stderr)
        return 1
    print(f"{args.image}: {bitmap.width}x{bitmap.height} dots "
          f"({bitmap.height / 203 * 25.4:.0f} mm of paper)")
    if args.preview:
        bitmap.convert("L").save(args.preview)
        print(f"preview written to {args.preview}")
    if args.output:
        from .raster import encode

        args.output.write_bytes(encode(bitmap, options.band_height))
        print(f"ESC/POS bytes written to {args.output}")
    return 0


def cmd_feed(settings, args) -> int:
    printer = make_printer(settings, args)
    try:
        printer.feed(args.lines)
    except (TransportError, OSError) as exc:
        print(f"feed failed: {exc}", file=sys.stderr)
        return 1
    finally:
        printer.transport.close()
    return 0


def cmd_cut(settings, args) -> int:
    printer = make_printer(settings, args)
    try:
        printer.cut()
    except (TransportError, OSError) as exc:
        print(f"cut failed: {exc}", file=sys.stderr)
        return 1
    finally:
        printer.transport.close()
    return 0


COMMANDS = {
    "print": cmd_print,
    "test": cmd_test,
    "diagnostics": cmd_diagnostics,
    "doctor": cmd_doctor,
    "status": cmd_status,
    "list": cmd_list,
    "render": cmd_render,
    "feed": cmd_feed,
    "cut": cmd_cut,
}


def main(argv: list[str] | None = None) -> int:
    _keep_output_printable()
    args = build_parser().parse_args(argv)
    settings = load_config(args)
    if args.command == "run":
        return app.run(settings)

    app.configure_logging("DEBUG" if args.verbose else "WARNING")
    for problem in settings.problems:
        print(f"config: {problem}", file=sys.stderr)
    return COMMANDS[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
