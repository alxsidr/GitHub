"""CLI entry point for MS Learn Agent."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from .catalog import fetch_learning_path, fetch_module, parse_input_url
from .exporters.audio_exporter import AudioExporter
from .exporters.docx_exporter import DocxExporter
from .models import Course
from .scraper import MSLearnScraper

log = logging.getLogger("mslearn")


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_course(url: str, output_dir: Path, locale: str) -> Course:
    """Fetch metadata and scrape content for the given URL."""
    content_type, slug, detected_locale = parse_input_url(url)
    locale = locale or detected_locale

    click.echo(f"Content type: {content_type}, slug: {slug}, locale: {locale}")

    # Fetch metadata from Catalog API
    click.echo("Fetching metadata from MS Learn Catalog API...")
    if content_type == "path":
        lp = fetch_learning_path(slug, locale)
        course = Course(
            title=lp.title,
            source_url=url,
            learning_path=lp,
            modules=lp.modules,
        )
    else:
        mod = fetch_module(slug, locale)
        course = Course(
            title=mod.title,
            source_url=url,
            modules=[mod],
        )

    total_units = sum(len(m.units) for m in course.modules)
    click.echo(f"Found {len(course.modules)} module(s), {total_units} unit(s)")

    # Scrape content
    scraper = MSLearnScraper(output_dir, locale)

    for mod in course.modules:
        click.echo(f"\n--- Module: {mod.title} ---")
        click.echo("  Discovering unit URLs...")
        scraper.populate_unit_urls(mod)

        for unit in mod.units:
            click.echo(f"  Fetching: {unit.title}...")
            scraper.fetch_unit_content(unit, mod.url.rstrip("/"))

        blocks_count = sum(len(u.content_blocks) for u in mod.units)
        click.echo(f"  Extracted {blocks_count} content blocks")

    return course


@click.group()
@click.version_option(version="0.1.0")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def cli(verbose: bool):
    """MS Learn Agent - Convert Microsoft Learn courses to Word docs and audio."""
    _setup_logging(verbose)


@cli.command()
@click.argument("url")
@click.option("-o", "--output", type=click.Path(), default="./output",
              help="Output directory")
@click.option("--locale", default="en-us", help="Locale (default: en-us)")
def docx(url: str, output: str, locale: str):
    """Fetch a MS Learn course and export to Word document.

    URL can be a learning path or module URL from learn.microsoft.com.
    """
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    course = _build_course(url, output_dir, locale)

    # Export to Word
    slug = url.rstrip("/").split("/")[-1]
    docx_path = output_dir / f"{slug}.docx"
    click.echo(f"\nGenerating Word document...")
    exporter = DocxExporter(docx_path)
    result = exporter.export(course)
    click.echo(f"Done! Saved to: {result}")


@cli.command()
@click.argument("url")
@click.option("-o", "--output", type=click.Path(), default="./output",
              help="Output directory")
@click.option("--voice", default="en-US-AriaNeural", help="TTS voice name")
@click.option("--locale", default="en-us", help="Locale (default: en-us)")
def audio(url: str, output: str, voice: str, locale: str):
    """Fetch a MS Learn course and export to MP3 audio files.

    Generates one MP3 per unit using Microsoft Edge TTS (free).
    """
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    course = _build_course(url, output_dir, locale)

    # Export to audio
    audio_dir = output_dir / "audio"
    click.echo(f"\nGenerating audio files...")
    exporter = AudioExporter(audio_dir, voice)
    results = exporter.export(course)
    click.echo(f"Done! Generated {len(results)} audio file(s) in: {audio_dir}")


@cli.command()
@click.argument("url")
@click.option("-o", "--output", type=click.Path(), default="./output",
              help="Output directory")
@click.option("--voice", default="en-US-AriaNeural", help="TTS voice name")
@click.option("--locale", default="en-us", help="Locale (default: en-us)")
def both(url: str, output: str, voice: str, locale: str):
    """Fetch and export to both Word document and audio."""
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    course = _build_course(url, output_dir, locale)

    # Word document
    slug = url.rstrip("/").split("/")[-1]
    docx_path = output_dir / f"{slug}.docx"
    click.echo(f"\nGenerating Word document...")
    docx_exporter = DocxExporter(docx_path)
    docx_result = docx_exporter.export(course)
    click.echo(f"Word document: {docx_result}")

    # Audio
    audio_dir = output_dir / "audio"
    click.echo(f"Generating audio files...")
    audio_exporter = AudioExporter(audio_dir, voice)
    audio_results = audio_exporter.export(course)
    click.echo(f"Audio: {len(audio_results)} file(s) in {audio_dir}")

    click.echo("\nAll done!")


if __name__ == "__main__":
    cli()
