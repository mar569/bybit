"""Tests for Urals oil snapshot parsing."""
from __future__ import annotations

from bot.urals_price import _parse_urals_from_html, urals_sparkline_from_brent


def test_parse_urals_from_meta_description():
    html = (
        '<meta name="description" content="Urals Oil rose to 85.39 USD/Bbl on July 29, 2026, '
        'up 18.76% from the previous day.">'
    )
    price, chg = _parse_urals_from_html(html)
    assert price == 85.39
    assert chg == 18.76


def test_urals_sparkline_tracks_brent_shape():
    brent = [90.0, 91.0, 89.5, 92.0]
    spark = urals_sparkline_from_brent(brent, urals_price=80.0, brent_last=92.0)
    assert len(spark) == 4
    assert abs(spark[-1] - 80.0) < 1e-6
    # same deltas as Brent
    assert abs((spark[1] - spark[0]) - (brent[1] - brent[0])) < 1e-6
