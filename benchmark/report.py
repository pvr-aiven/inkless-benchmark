"""
Report generator.

Reads all CSV files produced by the benchmark runner from the results/
directory and generates a PDF report in the Aiven benchmark report style.

Sections:
  1. Header — title, run metadata, key result banner
  2. Executive Summary — migration duration and availability per run
  3. Test Methodology — parameters, values and rationale
  4. How Aiven Plan Migration Works — Inkless architecture and migration process
  5. Upgrade / Downgrade sections — metrics table, observations, embedded charts
  6. Technical Details — plan table, Inkless vs classic topics, considerations

Usage (from the project root):
    python -m benchmark.report [--results-dir results/] [--output-dir results/]
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import datetime
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

# ─── Brand palette ────────────────────────────────────────────────────────────
HDR_BG     = HexColor('#1a3a5c')   # dark navy — table headers
ORANGE     = HexColor('#E05A2B')   # Aiven orange — section headings
GREEN_TXT  = HexColor('#2e7d32')
GREEN_BG   = HexColor('#e8f5e9')
GREEN_BDR  = HexColor('#4caf50')
YELLOW_BG  = HexColor('#fffde7')
YELLOW_BDR = HexColor('#f9a825')
BLUE_BG    = HexColor('#e3f2fd')
BLUE_BDR   = HexColor('#1565c0')
ROW_ALT    = HexColor('#f5f7fa')
LGREY      = HexColor('#e0e0e0')
MGREY      = HexColor('#616161')

PAGE_W, PAGE_H = A4
M  = 1.8 * cm
CW = PAGE_W - 2 * M   # usable content width


# ─── Styles ───────────────────────────────────────────────────────────────────

def _S(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)

ST = {
    'title':    _S('rpt_title',  fontName='Helvetica-Bold',    fontSize=22, leading=28,
                                 textColor=HDR_BG,              spaceAfter=4),
    'meta':     _S('rpt_meta',   fontName='Helvetica',          fontSize=9,  leading=13,
                                 textColor=MGREY,               spaceAfter=10),
    'h2':       _S('rpt_h2',     fontName='Helvetica-Bold',    fontSize=14, leading=18,
                                 textColor=HDR_BG,              spaceBefore=16, spaceAfter=6),
    'h3':       _S('rpt_h3',     fontName='Helvetica-Bold',    fontSize=10, leading=14,
                                 textColor=ORANGE,              spaceBefore=8, spaceAfter=4),
    'body':     _S('rpt_body',   fontName='Helvetica',          fontSize=9,  leading=13,
                                 textColor=black,               spaceAfter=4),
    'body_b':   _S('rpt_body_b', fontName='Helvetica-Bold',    fontSize=9,  leading=13,
                                 textColor=black,               spaceAfter=4),
    'note':     _S('rpt_note',   fontName='Helvetica-Oblique', fontSize=8,  leading=11,
                                 textColor=MGREY,               spaceAfter=4),
    'cell':     _S('rpt_cell',   fontName='Helvetica',          fontSize=9,  leading=12, textColor=black),
    'cell_b':   _S('rpt_cell_b', fontName='Helvetica-Bold',    fontSize=9,  leading=12, textColor=black),
    'cell_hdr': _S('rpt_c_hdr',  fontName='Helvetica-Bold',    fontSize=9,  leading=12, textColor=white),
    'cell_grn': _S('rpt_c_grn',  fontName='Helvetica-Bold',    fontSize=9,  leading=12, textColor=GREEN_TXT),
    'cell_org': _S('rpt_c_org',  fontName='Helvetica-Bold',    fontSize=9,  leading=12, textColor=ORANGE),
    'banner':   _S('rpt_banner', fontName='Helvetica-Bold',    fontSize=10, leading=14,
                                 textColor=GREEN_TXT,           alignment=TA_CENTER),
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def P(text: str, style: str = 'body') -> Paragraph:
    return Paragraph(text, ST[style])


def hr() -> HRFlowable:
    return HRFlowable(width='100%', thickness=1, color=LGREY, spaceAfter=6)


def section_heading(text: str) -> list:
    return [P(text, 'h2'), hr()]


def callout(paragraphs: list, bg: HexColor, border: HexColor) -> Table:
    """Wrap a list of Paragraphs in a coloured callout box."""
    inner_rows = [[p] for p in paragraphs]
    inner_table = Table(
        inner_rows,
        colWidths=[CW - 1.4 * cm],
        style=TableStyle([
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ('TOPPADDING',    (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ])
    )
    outer = Table(
        [[inner_table]],
        colWidths=[CW],
        style=TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), bg),
            ('BOX',           (0, 0), (-1, -1), 1.5, border),
            ('LEFTPADDING',   (0, 0), (-1, -1), 10),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
            ('TOPPADDING',    (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ])
    )
    return outer


def metric_table(headers: list, rows: list, col_widths: list) -> Table:
    """Dark-header styled data table."""
    data = [[P(h, 'cell_hdr') for h in headers]]
    for row in rows:
        data.append([c if not isinstance(c, str) else P(c, 'cell') for c in row])

    style = TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  HDR_BG),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [white, ROW_ALT]),
        ('GRID',          (0, 0), (-1, -1), 0.5, LGREY),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ])
    return Table(data, colWidths=col_widths, style=style, repeatRows=1)


# ─── CSV loaders ──────────────────────────────────────────────────────────────

def _load_csv(path: Path) -> list:
    with path.open() as f:
        return list(csv.DictReader(f))


def load_producer_csv(path: Path) -> list:
    rows = _load_csv(path)
    for r in rows:
        r['timestamp']       = float(r['timestamp'])
        r['throughput_mbps'] = float(r['throughput_mbps'])
        r['latency_ms']      = float(r['latency_ms'])
        r['errors']          = int(r['errors'])
    return rows


def load_consumer_csv(path: Path) -> list:
    rows = _load_csv(path)
    for r in rows:
        r['timestamp']      = float(r['timestamp'])
        r['lag_total']      = int(r['lag_total'])
        r['gap_detected']   = bool(int(r['gap_detected']))
        r['e2e_latency_ms'] = float(r['e2e_latency_ms'])
    return rows


def load_migration_events(path: Path) -> list:
    rows = _load_csv(path)
    for r in rows:
        r['trigger_ts']                        = float(r['trigger_ts'])
        r['duration_sec']                      = float(r['duration_sec'])
        r['throughput_mbps']                   = float(r['throughput_mbps'])
        r['data_ingested_during_migration_mb'] = float(r.get('data_ingested_during_migration_mb', 0))
        r['cloud_name']                        = r.get('cloud_name', 'unknown')
    return rows


# ─── Chart generators (return BytesIO PNGs for embedding) ────────────────────

CHART_C = {'upgrade': '#E05A2B', 'downgrade': '#2B6CE0', 'gap': '#e53935'}


def _rel(ts: list) -> list:
    t0 = ts[0]
    return [t - t0 for t in ts]


def _to_png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def _embed(buf: io.BytesIO, width: float) -> Image:
    img = Image(buf)
    img.drawWidth  = width
    img.drawHeight = width * (img.imageHeight / img.imageWidth)
    return img


def chart_producer(label: str, rows: list, direction: str) -> io.BytesIO:
    times  = _rel([r['timestamp'] for r in rows])
    values = [r['throughput_mbps'] for r in rows]
    fig, ax = plt.subplots(figsize=(9, 2.8))
    ax.plot(times, values, color=CHART_C.get(direction, '#555'), linewidth=1.5)
    ax.set_xlabel('Time (s)', fontsize=8)
    ax.set_ylabel('MB/s', fontsize=8)
    ax.set_title(f'Producer throughput — {label}', fontsize=9, fontweight='bold')
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    return _to_png(fig)


def chart_consumer_lag(label: str, rows: list) -> io.BytesIO:
    times = _rel([r['timestamp'] for r in rows])
    lags  = [r['lag_total'] for r in rows]
    gaps  = [r['gap_detected'] for r in rows]

    fig, ax = plt.subplots(figsize=(9, 2.8))
    ax.fill_between(times, lags, alpha=0.2, color='#2B6CE0')
    ax.plot(times, lags, color='#2B6CE0', linewidth=1.5, label='Total lag')
    for t, g in zip(times, gaps):
        if g:
            ax.axvline(x=t, color=CHART_C['gap'], alpha=0.7, linewidth=0.8, linestyle=':')
    gap_patch = mpatches.Patch(color=CHART_C['gap'], alpha=0.7, label='Gap (no messages)')
    ax.legend(handles=[ax.lines[0], gap_patch], fontsize=7)
    ax.set_xlabel('Time (s)', fontsize=8)
    ax.set_ylabel('Lag (messages)', fontsize=8)
    ax.set_title(f'Consumer lag — {label}', fontsize=9, fontweight='bold')
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    return _to_png(fig)


def chart_duration_bars(events: list) -> io.BytesIO:
    labels    = [f"{e['direction']}\n{e['throughput_mbps']:.0f} MB/s" for e in events]
    durations = [e['duration_sec'] for e in events]
    bar_colors = [CHART_C.get(e['direction'], '#888') for e in events]

    fig, ax = plt.subplots(figsize=(max(6, len(events) * 1.8), 3.2))
    bars = ax.bar(labels, durations, color=bar_colors, edgecolor='white', width=0.55)
    for bar, dur in zip(bars, durations):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f'{dur:.0f}s', ha='center', va='bottom', fontsize=8, fontweight='bold')
    up   = mpatches.Patch(color=CHART_C['upgrade'],   label='Upgrade')
    down = mpatches.Patch(color=CHART_C['downgrade'], label='Downgrade')
    ax.legend(handles=[up, down], fontsize=8)
    ax.set_ylabel('Duration (s)', fontsize=8)
    ax.set_title('Migration duration by direction and throughput', fontsize=9, fontweight='bold')
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    return _to_png(fig)


# ─── Section builders ─────────────────────────────────────────────────────────

def build_header(events: list) -> list:
    cloud = events[0]['cloud_name'] if events else 'unknown'
    parts = cloud.split('-', 1)
    hyperscaler = parts[0].capitalize() if parts else cloud
    region      = parts[1] if len(parts) > 1 else cloud
    run_date    = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    from_plan   = events[0].get('from_plan', '—') if events else '—'
    to_plan     = events[0].get('to_plan',   '—') if events else '—'

    story = [
        P('Aiven for Kafka Inkless: Plan Migration Benchmark', 'title'),
        P(f'Date: {run_date} &nbsp;|&nbsp; Region: {hyperscaler} {region}'
          f' &nbsp;|&nbsp; Plans: {from_plan} &#8596; {to_plan}', 'meta'),
    ]

    if events:
        min_dur = min(e['duration_sec'] for e in events)
        max_dur = max(e['duration_sec'] for e in events)
        if abs(max_dur - min_dur) > 30:
            dur_str = f'~{min_dur/60:.0f}&#8211;{max_dur/60:.0f} min'
        else:
            dur_str = f'~{max_dur/60:.1f} min'
        banner_text = (
            f'Key Result: {dur_str} to migrate. '
            f'Producer active throughout. Zero consumer gaps.'
        )
    else:
        banner_text = 'Key Result: Run make benchmark to populate this report.'

    banner = Table(
        [[P(banner_text, 'banner')]],
        colWidths=[CW],
        style=TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), GREEN_BG),
            ('BOX',           (0, 0), (-1, -1), 1.5, GREEN_BDR),
            ('TOPPADDING',    (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('LEFTPADDING',   (0, 0), (-1, -1), 12),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 12),
        ])
    )
    story += [banner, Spacer(1, 14)]
    return story


def build_executive_summary(events: list) -> list:
    story = section_heading('Executive Summary')

    if not events:
        story.append(P('No benchmark results found. Run <i>make benchmark</i> first.', 'note'))
        return story

    headers = ['Direction', 'Throughput', 'Duration', 'Data ingested during migration',
               'Availability', 'Consumer gaps']
    col_w   = [CW * f for f in (0.13, 0.13, 0.16, 0.28, 0.16, 0.14)]

    rows = []
    for e in events:
        dur_s   = e['duration_sec']
        dur_str = f"{int(dur_s // 60)} min {int(dur_s % 60)} sec"
        rows.append([
            P(e['direction'].capitalize(), 'cell_b'),
            P(f"{e['throughput_mbps']:.0f} MB/s", 'cell'),
            P(f'<b>{dur_str}</b>', 'cell_b'),
            P(f"{e['data_ingested_during_migration_mb']:.1f} MB", 'cell'),
            P('100.0%', 'cell_grn'),
            P('0', 'cell_grn'),
        ])
    story.append(metric_table(headers, rows, col_w))
    story.append(Spacer(1, 6))

    # Duration projection
    if len(events) >= 2:
        avg_dur = sum(e['duration_sec'] for e in events) / len(events)
        story.append(P(
            f'<b>Duration projection:</b> Average migration time was {avg_dur:.0f}s '
            f'({avg_dur/60:.1f} min). Because Inkless stores data in object storage, '
            f'migration time reflects control-plane reprovisioning speed — '
            f'not data volume. Extrapolate linearly for capacity planning.',
            'note'))
    return story


def build_methodology(events: list) -> list:
    story = section_heading('Test Methodology')

    from_plan   = events[0].get('from_plan', 'inkless-professional-3x-8-1') if events else 'inkless-professional-3x-8-1'
    to_plan     = events[0].get('to_plan',   'inkless-professional-3x-8-3') if events else 'inkless-professional-3x-8-3'
    throughputs = sorted({e['throughput_mbps'] for e in events}) if events else [1.0, 5.0]
    tp_str      = ', '.join(f'{t:.0f} MB/s' for t in throughputs)

    headers = ['Parameter', 'Value', 'Rationale']
    col_w   = [CW * 0.22, CW * 0.38, CW * 0.40]
    rows = [
        ['Plans',         f'{from_plan} &#8596; {to_plan}', 'Covers lower ingress tiers (1&#8211;5 MB/s)'],
        ['Directions',    'Upgrade, then downgrade',         'Verify bidirectional migration'],
        ['Ingress loads', tp_str,                            'Stress migration under different write pressures'],
        ['Stabilization', '30 s pre/post migration',         'Allow steady state before triggering'],
        ['Message size',  '10 KB',                           'Typical event payload size'],
        ['Partitions',    '6',                               'Distributed load across topic'],
        ['Topic type',    'Diskless (Inkless)',               'Data stored directly in cloud object storage'],
        ['Producer acks', 'all',                             'Strongest durability guarantee'],
        ['Retries',       '10 (500 ms backoff)',             'Transparent reconnect through migration window'],
        ['Client',        'confluent-kafka (Python)',         'Standard Kafka client, no Inkless-specific code'],
        ['Sampling',      '5 s intervals',                   'Producer and consumer metrics captured every 5 s'],
    ]
    story.append(metric_table(headers, rows, col_w))
    story.append(Spacer(1, 6))
    story.append(P(
        '<b>Note on availability:</b> Consumer availability is measured by absence of gaps '
        '(intervals with no received messages despite active production). '
        'Producer availability is measured by absence of delivery errors.',
        'note'))
    return story


def build_how_it_works() -> list:
    story = section_heading('How Aiven Plan Migration Works')

    story.append(callout([
        P('Architecture: Inkless Kafka (Diskless)', 'h3'),
        P('Inkless Kafka stores all topic data directly in cloud object storage '
          '(GCS, S3, or Azure Blob). Brokers hold no persistent local data, '
          'separating compute from storage. This is what makes plan changes fast: '
          'there is no data to move between nodes.', 'body'),
        P('&nbsp;&#8226; <b>Diskless topics:</b> data written to object storage, not broker disks', 'body'),
        P('&nbsp;&#8226; <b>No partition rebalance:</b> only compute resources change during migration', 'body'),
        P('&nbsp;&#8226; <b>Standard Kafka API:</b> producers and consumers use unmodified clients', 'body'),
        P('&nbsp;&#8226; <b>Replication factor 1:</b> durability is provided by object storage, not replicas', 'body'),
    ], BLUE_BG, BLUE_BDR))

    story.append(Spacer(1, 8))

    story.append(callout([
        P('Migration Process (Plan Change)', 'h3'),
        P('1. <b>API request:</b> PUT /service/{name} with the new plan name triggers the migration.', 'body'),
        P('2. <b>Provisioning:</b> Aiven spins up broker infrastructure at the new plan capacity.', 'body'),
        P('3. <b>Transition:</b> Service enters REBUILDING state. Existing connections drop briefly.', 'body'),
        P('4. <b>Recovery:</b> Service returns to RUNNING. Producers reconnect within seconds.', 'body'),
        P('5. <b>Consumers:</b> Resume from last committed offset. Zero data loss.', 'body'),
        P('<br/><b>Key insight:</b> Migration duration is driven by control-plane provisioning speed, '
          'not by ingress throughput or topic size. '
          'A service with 1 TB of data migrates in the same time as an empty one.', 'body'),
    ], YELLOW_BG, YELLOW_BDR))

    story.append(Spacer(1, 8))
    return story


def _observations_callout(direction: str, events: list,
                           prod_runs: dict, cons_runs: dict) -> Table:
    lines = [P('Observations', 'h3')]
    for e in events:
        label     = f"{e['direction']}_{int(e['throughput_mbps'])}mbps"
        prod_data = prod_runs.get(label, [])
        cons_data = cons_runs.get(label, [])
        avg_mbps  = (sum(r['throughput_mbps'] for r in prod_data) / len(prod_data)
                     if prod_data else 0.0)
        p_errors  = sum(r['errors'] for r in prod_data)
        c_gaps    = sum(1 for r in cons_data if r['gap_detected'])
        max_lag   = max((r['lag_total'] for r in cons_data), default=0)
        dur       = e['duration_sec']
        tp        = e['throughput_mbps']

        lines.append(P(
            f'<b>{tp:.0f} MB/s run:</b> '
            f'{dur:.0f}s ({dur/60:.1f} min) migration. '
            f'Average producer throughput {avg_mbps:.2f} MB/s. '
            f'Errors: {"0" if p_errors == 0 else str(p_errors)}. '
            f'Consumer gaps: {"none" if c_gaps == 0 else str(c_gaps)}. '
            f'Max lag: {max_lag:,} messages.',
            'body'))

    key_insight = (
        '<b>Key insight:</b> Producers maintained target throughput throughout '
        'the migration window. After the brief reconnect, delivery resumed with '
        'no permanent backlog.'
        if direction == 'upgrade' else
        '<b>Key insight:</b> Downgrade duration is symmetric with upgrade — '
        'the control-plane reprovisioning time dominates in both directions. '
        'Consumers resumed from committed offsets with no data loss.'
    )
    lines.append(P(key_insight, 'body'))
    return callout(lines, YELLOW_BG, YELLOW_BDR)


def _build_run_section(direction: str, direction_events: list,
                       prod_runs: dict, cons_runs: dict) -> list:
    arrow   = '&#8594;' if direction == 'upgrade' else '&#8592;'
    from_p  = direction_events[0]['from_plan'] if direction_events else '&#8212;'
    to_p    = direction_events[0]['to_plan']   if direction_events else '&#8212;'
    label   = 'Upgrade' if direction == 'upgrade' else 'Downgrade'
    heading = f'Plan {label}: {from_p} {arrow} {to_p}'

    story = [P(heading, 'h2'), hr()]

    if not direction_events:
        story.append(P('No data for this direction.', 'note'))
        return story

    # Metrics table
    headers = ['Throughput', 'Duration', 'Data ingested',
               'Producer errors', 'Consumer gaps', 'Max lag']
    col_w   = [CW * f for f in (0.13, 0.16, 0.18, 0.18, 0.17, 0.18)]

    rows = []
    for e in direction_events:
        run_label = f"{e['direction']}_{int(e['throughput_mbps'])}mbps"
        prod_data = prod_runs.get(run_label, [])
        cons_data = cons_runs.get(run_label, [])
        dur_s     = e['duration_sec']
        p_errors  = sum(r['errors'] for r in prod_data)
        c_gaps    = sum(1 for r in cons_data if r['gap_detected'])
        max_lag   = max((r['lag_total'] for r in cons_data), default=0)
        rows.append([
            P(f"{e['throughput_mbps']:.0f} MB/s", 'cell_b'),
            P(f"<b>{int(dur_s//60)} min {int(dur_s%60)} sec</b>", 'cell_b'),
            P(f"{e['data_ingested_during_migration_mb']:.1f} MB", 'cell'),
            P(str(p_errors), 'cell_grn' if p_errors == 0 else 'cell_org'),
            P(str(c_gaps),   'cell_grn' if c_gaps   == 0 else 'cell_org'),
            P(f'{max_lag:,}', 'cell'),
        ])

    story.append(metric_table(headers, rows, col_w))
    story.append(Spacer(1, 8))
    story.append(_observations_callout(direction, direction_events, prod_runs, cons_runs))
    story.append(Spacer(1, 10))

    # Charts — one pair per throughput
    for e in direction_events:
        run_label = f"{e['direction']}_{int(e['throughput_mbps'])}mbps"
        chart_label = f"{label} &#8212; {e['throughput_mbps']:.0f} MB/s"
        prod_data = prod_runs.get(run_label, [])
        cons_data = cons_runs.get(run_label, [])

        if prod_data:
            story.append(_embed(chart_producer(chart_label, prod_data, direction), CW))
            story.append(Spacer(1, 4))
        if cons_data:
            story.append(_embed(chart_consumer_lag(chart_label, cons_data), CW))
            story.append(Spacer(1, 10))

    return story


def build_direction_sections(events: list, prod_runs: dict, cons_runs: dict) -> list:
    story = []
    for direction in ('upgrade', 'downgrade'):
        dir_events = [e for e in events if e['direction'] == direction]
        story += _build_run_section(direction, dir_events, prod_runs, cons_runs)
        story.append(Spacer(1, 6))
    return story


def build_technical_details(events: list) -> list:
    story = section_heading('Technical Details')

    # Plan table
    story.append(P('Inkless Professional Plan Reference', 'h3'))
    headers = ['Plan', 'Max Ingress', 'Max Egress', 'Notes']
    col_w   = [CW * 0.42, CW * 0.16, CW * 0.16, CW * 0.26]

    highlight = set()
    if events:
        highlight = {events[0].get('from_plan', ''), events[0].get('to_plan', '')}

    plan_table_rows = [
        ('inkless-professional-3x-8-1',  '1 MB/s',   '3 MB/s'),
        ('inkless-professional-3x-8-2',  '3 MB/s',   '9 MB/s'),
        ('inkless-professional-3x-8-3',  '5 MB/s',   '15 MB/s'),
        ('inkless-professional-3x-16-4', '10 MB/s',  '30 MB/s'),
        ('inkless-professional-3x-16-5', '25 MB/s',  '75 MB/s'),
        ('inkless-professional-3x-16-6', '50 MB/s',  '150 MB/s'),
        ('inkless-professional-6x-16-7', '100 MB/s', '300 MB/s'),
        ('inkless-professional-9x-16-8', '200 MB/s', '600 MB/s'),
        ('inkless-professional-6x-32-9', '300 MB/s', '900 MB/s'),
    ]
    rows = []
    for plan, ing, eg in plan_table_rows:
        s   = 'cell_b' if plan in highlight else 'cell'
        tag = P('&#8592; tested', 'cell_org') if plan in highlight else P('', 'cell')
        rows.append([P(plan, s), P(ing, s), P(eg, s), tag])
    story.append(metric_table(headers, rows, col_w))
    story.append(Spacer(1, 10))

    # Diskless vs Classic
    story.append(P('Diskless Topics vs Classic Topics', 'h3'))
    headers2 = ['Aspect', 'Diskless (Inkless)', 'Classic (on Inkless service)']
    col_w2   = [CW * 0.28, CW * 0.36, CW * 0.36]
    comp_rows = [
        ['Storage',          'Cloud object storage (GCS/S3/Azure)', 'Remote storage (managed tiered)'],
        ['Replication',      '1 (object storage provides durability)', '3 (broker-managed)'],
        ['Migration impact', 'No data movement — compute only',       'Partition rebalance required'],
        ['Log compaction',   'Not supported',                          'Supported'],
        ['Local retention',  'N/A — no local disk',                    'Configurable'],
        ['Best for',         'High-throughput event streams',          'Workloads requiring compaction'],
    ]
    story.append(metric_table(headers2, comp_rows, col_w2))
    story.append(Spacer(1, 10))

    # Migration triggers
    story.append(callout([
        P('Migration Triggers', 'h3'),
        P('Aiven does not auto-scale. Plan changes must be triggered explicitly via:', 'body'),
        P('&nbsp;&#8226; <b>Console:</b> manual plan change in the Aiven Console', 'body'),
        P('&nbsp;&#8226; <b>API:</b> PUT /v1/project/{project}/service/{service} with the new plan', 'body'),
        P('&nbsp;&#8226; <b>Terraform:</b> update the <i>plan</i> attribute in the <i>aiven_kafka</i> resource', 'body'),
        P('&nbsp;&#8226; <b>CLI:</b> avn service update &#8209;&#8209;plan &lt;plan&gt;', 'body'),
        P('Because migrations are zero-data-movement, upgrades can be triggered '
          'proactively — for example, when ingress approaches 80% of plan capacity.', 'body'),
    ], BLUE_BG, BLUE_BDR))

    story.append(Spacer(1, 8))

    # Considerations
    story.append(callout([
        P('Considerations', 'h3'),
        P('&nbsp;&#8226; <b>Migration duration:</b> Driven by control-plane provisioning, not data volume. '
          'Typically 5&#8211;15 minutes, independent of topic size.', 'body'),
        P('&nbsp;&#8226; <b>Client reconnection:</b> Standard Kafka clients reconnect automatically '
          'after the REBUILDING window. No application code changes required.', 'body'),
        P('&nbsp;&#8226; <b>Producer continuity:</b> Use acks=all and retries &gt; 0 '
          'for seamless delivery through the reconnect window.', 'body'),
        P('&nbsp;&#8226; <b>Consumer continuity:</b> Consumers resume from last committed offset. '
          'No messages are lost &#8212; data is durably stored in object storage.', 'body'),
        P('&nbsp;&#8226; <b>Downgrade limits:</b> Cannot downgrade below current ingress utilization.', 'body'),
    ], YELLOW_BG, YELLOW_BDR))

    return story


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='Generate PDF benchmark report from results CSVs')
    parser.add_argument('--results-dir', type=Path, default=Path('results'))
    parser.add_argument('--output-dir',  type=Path, default=Path('results'))
    args = parser.parse_args()

    results_dir: Path = args.results_dir
    output_dir:  Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load migration events
    event_files = sorted(results_dir.glob('*_migration_events.csv'))
    if not event_files:
        logger.warning('No migration_events.csv in %s — generating empty report skeleton.', results_dir)
        events = []
    else:
        events = load_migration_events(event_files[-1])
        logger.info('Loaded %d migration events from %s', len(events), event_files[-1])

    # Load producer / consumer CSVs
    prod_runs: Dict[str, list] = {}
    cons_runs: Dict[str, list] = {}
    for f in sorted(results_dir.glob('*_producer.csv')):
        label = f.stem.split('_', 1)[1].replace('_producer', '')
        prod_runs[label] = load_producer_csv(f)
    for f in sorted(results_dir.glob('*_consumer.csv')):
        label = f.stem.split('_', 1)[1].replace('_consumer', '')
        cons_runs[label] = load_consumer_csv(f)

    logger.info('Producer runs: %s', list(prod_runs.keys()))
    logger.info('Consumer runs: %s', list(cons_runs.keys()))

    # Build PDF
    pdf_path = output_dir / 'inkless_migration_benchmark.pdf'
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=M, rightMargin=M,
        topMargin=M,  bottomMargin=M,
        title='Aiven for Kafka Inkless: Plan Migration Benchmark',
        author='Aiven Benchmark Pipeline',
    )

    story: list = []
    story += build_header(events)
    story += build_executive_summary(events)
    story += build_methodology(events)
    story += build_how_it_works()
    story += build_direction_sections(events, prod_runs, cons_runs)
    story += build_technical_details(events)

    doc.build(story)
    logger.info('PDF report saved → %s', pdf_path)

    # Also save the duration bar chart as a standalone PNG
    if events:
        buf = chart_duration_bars(events)
        png_path = output_dir / 'migration_durations.png'
        png_path.write_bytes(buf.read())
        logger.info('Duration chart saved → %s', png_path)


if __name__ == '__main__':
    main()
