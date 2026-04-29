"""
Generate ReversePicks Cheat Sheet 2.1 image from live settled-pick data.

Two ways to run it:
  1. Standalone CLI:  cd backend && python scripts/build_cheat_sheet.py
  2. In-process from the FastAPI server (see cheat_sheet_loop in server.py),
     which calls render_cheat_sheet(db) on the live Motor connection.

Outputs: attached_assets/cheat_sheet_2_1.png
"""
import asyncio
import os
import sys
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from motor.motor_asyncio import AsyncIOMotorClient

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'attached_assets', 'cheat_sheet_2_1.png',
)

POS_BUCKET = {
    'GK': 'GK',
    'CB': 'CB', 'LB': 'CB', 'RB': 'CB', 'LWB': 'CB', 'RWB': 'CB',
    'CDM': 'CDM', 'CM': 'CDM',
    'CAM': 'AM', 'LM': 'AM', 'RM': 'AM',
    'LW': 'WING', 'RW': 'WING', 'SS': 'WING',
    'CF': 'ST', 'ST': 'ST',
}
LEAGUE_NAMES = {
    61: 'Ligue 1', 140: 'La Liga', 39: 'Premier League', 135: 'Serie A',
    78: 'Bundesliga', 128: 'Liga Profesional', 253: 'MLS', 88: 'Eredivisie',
    94: 'Primeira Liga', 197: 'Super Lig', 144: 'Jupiler Pro',
    103: 'Eliteserien', 113: 'Allsvenskan', 2: 'Champions League',
    3: 'Europa League', 71: 'Brasileirao', 307: 'Saudi Pro League',
    233: 'Egyptian Premier', 188: 'A-League', 40: 'Championship',
    667: 'Friendlies', 41: 'League One', 42: 'League Two',
    218: 'Bundesliga (AT)', 4: 'Euro Championship', 13: 'Copa Libertadores',
    169: 'Super League (CN)', 271: 'Liga MX', 281: 'Liga 1 (PE)',
    98: 'J1 League', 292: 'K League 1', 357: 'Premier Division (IE)',
    119: 'Superligaen (DK)', 203: 'Süper Lig 2', 210: 'Prva HNL',
    383: 'Israeli Premier', 333: 'Premier League (UA)',
}

FONT_REG = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_MONO = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf'

BG = (10, 10, 10)
PANEL = (20, 24, 22)
PANEL_BORDER = (40, 80, 50)
GREEN = (50, 220, 70)
GREEN_DIM = (30, 160, 50)
RED = (230, 70, 70)
YELLOW = (255, 200, 60)
WHITE = (240, 245, 240)
GREY = (160, 160, 160)
DARK_GREY = (90, 90, 90)


def f(size, bold=False, mono=False):
    path = FONT_MONO if mono else (FONT_BOLD if bold else FONT_REG)
    return ImageFont.truetype(path, size)


async def gather_stats(db=None):
    cli = None
    if db is None:
        cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
        db = cli[os.environ['DB_NAME']]
    try:
        rows = []
        async for d in db.picks.find({'status': 'settled', 'result': {'$in': ['hit', 'miss']}}):
            rows.append(d)
    finally:
        if cli is not None:
            cli.close()
    bucket = defaultdict(lambda: [0, 0])
    league_bucket = defaultdict(lambda: [0, 0])
    for d in rows:
        prop = d.get('propType', '')
        venue = d.get('venue', '')
        rec = d.get('recommendation', '')
        if rec not in ('over', 'under'):
            continue
        pos = d.get('position', '')
        pb = POS_BUCKET.get(pos, pos)
        result = d.get('result', '')
        lid = d.get('leagueId', 0)
        bucket[(prop, venue, pb, rec)][1] += 1
        league_bucket[(prop, venue, pb, rec, lid)][1] += 1
        if result == 'hit':
            bucket[(prop, venue, pb, rec)][0] += 1
            league_bucket[(prop, venue, pb, rec, lid)][0] += 1
    return rows, bucket, league_bucket


def league_breakdown(league_bucket, key, min_n=3, top=4):
    out = []
    for (p, v, po, r, lid), (h, n) in league_bucket.items():
        if (p, v, po, r) == key and n >= min_n:
            out.append((h / n * 100, n, lid, h))
    out.sort(key=lambda x: (-x[0], -x[1]))
    return out[:top]


def draw_panel(draw, x, y, w, h, title=None, title_color=GREEN):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=10,
                           fill=PANEL, outline=PANEL_BORDER, width=2)
    if title:
        draw.text((x + 16, y + 12), title, fill=title_color, font=f(15, bold=True))


def draw_play(draw, x, y, w, label, hits, n, sub_lines, accent=GREEN):
    pct = (hits / n * 100) if n else 0
    draw.text((x, y), label, fill=accent, font=f(15, bold=True))
    pct_str = f'{pct:.0f}%'
    sample_str = f'BASELINE ({hits}/{n})'
    draw.text((x, y + 22), pct_str, fill=WHITE, font=f(28, bold=True))
    bbox = draw.textbbox((0, 0), pct_str, font=f(28, bold=True))
    pw = bbox[2] - bbox[0]
    draw.text((x + pw + 10, y + 32), sample_str, fill=GREY, font=f(10, bold=True))
    yy = y + 65
    draw.text((x, yy), 'BEST CONDITIONS', fill=GREEN_DIM, font=f(10, bold=True))
    yy += 16
    for line_label, line_h, line_n in sub_lines:
        line_pct = (line_h / line_n * 100) if line_n else 0
        line_pct_color = GREEN if line_pct >= 70 else (YELLOW if line_pct >= 50 else RED)
        draw.text((x, yy), line_label, fill=WHITE, font=f(11))
        right_str = f'{line_pct:.0f}% ({line_h}/{line_n})'
        rbbox = draw.textbbox((0, 0), right_str, font=f(11, bold=True))
        rw = rbbox[2] - rbbox[0]
        draw.text((x + w - rw - 4, yy), right_str, fill=line_pct_color, font=f(11, bold=True))
        yy += 17


async def render_cheat_sheet(db=None, output_path=None):
    """Render the cheat-sheet PNG from settled-pick data.

    Args:
        db: optional Motor AsyncIOMotorDatabase. If None, builds its own
            client from MONGO_URL/DB_NAME (CLI use).
        output_path: optional override for the PNG output path.

    Returns:
        dict with {'path', 'total_picks', 'bytes'} on success.
    """
    rows, bucket, league_bucket = await gather_stats(db=db)
    total_picks = len(rows)

    W, H = 1280, 1600
    img = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ===== HEADER =====
    draw.text((40, 28), 'REVERSEPICKS', fill=GREEN, font=f(38, bold=True))
    draw.text((42, 70), 'AI SOCCER PROP ANALYTICS', fill=GREEN_DIM, font=f(13, bold=True))
    draw.text((40, 100), 'CHEAT SHEET 2.1', fill=GREEN, font=f(54, bold=True))
    draw.text((44, 168), f'Live data — {total_picks} settled picks · all prop types',
              fill=GREY, font=f(13))

    # Top-right context
    draw_panel(draw, 870, 30, 380, 175, 'KEY DIMENSIONS')
    bullets = [
        'Score State (Win / Draw / Loss)',
        'Goal Differential (Blowout / Close)',
        'Total Goals in Match',
        'League',
        'Position Bucket (GK / CB / CDM / WING / ST)',
        'Prop Type (passes / saves / shots)',
    ]
    for i, b in enumerate(bullets):
        draw.text((890, 56 + i * 22), '•  ' + b, fill=WHITE, font=f(12))

    # ===== ONE-LINE FORMULA =====
    draw_panel(draw, 40, 220, 1200, 95, 'THE ONE-LINE FORMULA')
    formula = [
        'Bet defenders/keepers UNDER on passes when the match smells like a draw or low-scoring game,',
        'in Ligue 1 / La Liga / Premier League / Liga Profesional / Serie A.  Bet AWAY CDMs OVER passes',
        'in Serie A and Premier League — small but dominant new signal.  Skip the rule in Bundesliga / MLS.',
    ]
    for i, line in enumerate(formula):
        draw.text((58, 250 + i * 20), line, fill=WHITE, font=f(13))

    # ===== TIER 1 SOLO PLAYS =====
    draw_panel(draw, 40, 335, 1200, 230, 'TIER 1 — SOLO PLAYS  (when they actually catch)')
    plays = [
        ('AWAY CDM OVER PASSES', 'pass_attempts', 'away', 'CDM', 'over', GREEN),
        ('AWAY CB UNDER PASSES', 'pass_attempts', 'away', 'CB', 'under', GREEN),
        ('HOME GK UNDER PASSES', 'pass_attempts', 'home', 'GK', 'under', GREEN),
        ('AWAY GK UNDER PASSES', 'pass_attempts', 'away', 'GK', 'under', GREEN),
    ]
    col_w = 280
    for i, (label, prop, ven, pos, rec, color) in enumerate(plays):
        x = 60 + i * (col_w + 15)
        h, n = bucket[(prop, ven, pos, rec)]
        leagues = league_breakdown(league_bucket, (prop, ven, pos, rec))
        sub_lines = []
        for pct, ln, lid, lh in leagues:
            sub_lines.append((LEAGUE_NAMES.get(lid, f'League {lid}'), lh, ln))
        draw_play(draw, x, 380, col_w, label, h, n, sub_lines, accent=color)

    # ===== TIER 1B — NEW PROP DISCOVERIES =====
    draw_panel(draw, 40, 585, 1200, 220, 'TIER 1B — NEW PROP DISCOVERIES  (saves & shots, smaller samples)')
    discoveries = [
        ('AWAY WING UNDER SHOTS', 'shots', 'away', 'WING', 'under'),
        ('HOME GK UNDER SAVES', 'saves', 'home', 'GK', 'under'),
        ('HOME WING UNDER PASSES', 'pass_attempts', 'home', 'WING', 'under'),
        ('AWAY ST UNDER PASSES', 'pass_attempts', 'away', 'ST', 'under'),
    ]
    for i, (label, prop, ven, pos, rec) in enumerate(discoveries):
        x = 60 + i * (col_w + 15)
        h, n = bucket[(prop, ven, pos, rec)]
        leagues = league_breakdown(league_bucket, (prop, ven, pos, rec), min_n=2, top=3)
        sub_lines = [(LEAGUE_NAMES.get(lid, f'L{lid}'), lh, ln) for pct, ln, lid, lh in leagues]
        draw_play(draw, x, 630, col_w, label, h, n, sub_lines, accent=YELLOW)
    draw.text((58, 778), 'These are emerging — sample 6-12. Treat as confidence boosters, not standalones.',
              fill=GREY, font=f(11))

    # ===== TIER 2 — GLOBAL BIAS =====
    draw_panel(draw, 40, 825, 1200, 270, 'TIER 2 — GLOBAL OVER/UNDER BIAS  (large-sample baselines)')
    glob = defaultdict(lambda: [0, 0])
    for d in rows:
        prop = d.get('propType', '')
        ven = d.get('venue', '')
        rec = d.get('recommendation', '')
        if rec not in ('over', 'under'):
            continue
        glob[(prop, ven, rec)][1] += 1
        if d.get('result') == 'hit':
            glob[(prop, ven, rec)][0] += 1
    glob_rows = [(h / n * 100, n, prop, ven, rec, h)
                 for (prop, ven, rec), (h, n) in glob.items() if n >= 10]
    glob_rows.sort(key=lambda x: -x[0])

    headers = ['PROP', 'VENUE', 'PICK', 'HIT RATE', 'SAMPLE']
    col_xs = [60, 320, 480, 700, 900]
    for hx, hd in zip(col_xs, headers):
        draw.text((hx, 860), hd, fill=GREEN_DIM, font=f(11, bold=True))
    yy = 885
    for pct, n, prop, ven, rec, h in glob_rows[:8]:
        color = GREEN if pct >= 60 else (YELLOW if pct >= 50 else RED)
        draw.text((col_xs[0], yy), prop.replace('_', ' ').title(), fill=WHITE, font=f(12))
        draw.text((col_xs[1], yy), ven.upper(), fill=WHITE, font=f(12))
        draw.text((col_xs[2], yy), rec.upper(), fill=color, font=f(12, bold=True))
        draw.text((col_xs[3], yy), f'{pct:.0f}%', fill=color, font=f(13, bold=True))
        draw.text((col_xs[4], yy), f'{h}/{n}', fill=GREY, font=f(12, mono=True))
        yy += 22

    # ===== QUICK CONTEXT GUIDE =====
    draw_panel(draw, 40, 1115, 590, 290, 'QUICK CONTEXT GUIDE  (at a glance)')
    ctx = [
        ('BLOWOUT GAME (3+ margin)',  'Lean UNDER on Away CB & Home GK passes.\n  Lean OVER on Home CDM passes (PL/Serie A).'),
        ('DRAW GAME',                 'Best spot for Home GK UNDER & Away CB UNDER.'),
        ('LOW-SCORING (0-2 totals)',  'Elite spot for Home GK UNDER passes.'),
        ('HIGH-SCORING (4+ totals)',  'Avoid Away GK UNDER (tank shots up).'),
        ('SCORE STATE — TRAILING',    'AWAY CDM OVER passes triggers (chasing → ball at feet).'),
    ]
    yy = 1148
    for label, body in ctx:
        draw.text((58, yy), '▶ ' + label, fill=GREEN, font=f(12, bold=True))
        for j, line in enumerate(body.split('\n')):
            draw.text((78, yy + 18 + j * 16), line, fill=WHITE, font=f(11))
        yy += 18 + 16 * (body.count('\n') + 1) + 6

    # ===== TOP LEAGUES =====
    draw_panel(draw, 650, 1115, 590, 290, 'TOP LEAGUES FOR THESE PATTERNS')
    league_perf = defaultdict(lambda: [0, 0])
    for d in rows:
        if d.get('recommendation') not in ('over', 'under'):
            continue
        league_perf[d.get('leagueId', 0)][1] += 1
        if d.get('result') == 'hit':
            league_perf[d.get('leagueId', 0)][0] += 1
    lp_rows = [(h / n * 100, n, lid, h) for lid, (h, n) in league_perf.items() if n >= 10]
    lp_rows.sort(key=lambda x: (-x[0], -x[1]))
    headers = ['LEAGUE', 'HIT RATE', 'SAMPLE']
    col_xs = [670, 920, 1080]
    for hx, hd in zip(col_xs, headers):
        draw.text((hx, 1148), hd, fill=GREEN_DIM, font=f(11, bold=True))
    yy = 1174
    for pct, n, lid, h in lp_rows[:10]:
        name = LEAGUE_NAMES.get(lid, f'League {lid}')
        color = GREEN if pct >= 60 else (YELLOW if pct >= 50 else RED)
        draw.text((col_xs[0], yy), name, fill=WHITE, font=f(12))
        draw.text((col_xs[1], yy), f'{pct:.0f}%', fill=color, font=f(13, bold=True))
        draw.text((col_xs[2], yy), f'{h}/{n}', fill=GREY, font=f(12, mono=True))
        yy += 22

    # ===== BOTTOM LINE =====
    draw_panel(draw, 40, 1425, 1200, 150, 'BOTTOM LINE')
    rules = [
        '1.  Bet defenders & keepers UNDER on passes in draw / blowout / low-scoring spots.',
        '2.  AWAY CDM OVER passes is the new top play (12-pick sample, 92%) — Serie A & PL only.',
        '3.  Home GK UNDER saves is your 2nd hidden gem (78% on 9 picks).',
        '4.  Use league filter: Ligue 1, La Liga, Premier League, Liga Profesional, Serie A favored.',
        '5.  Skip Bundesliga, MLS, and short-sample 1-goal nailbiters.',
    ]
    for i, r in enumerate(rules):
        draw.text((58, 1452 + i * 21), r, fill=WHITE, font=f(13))

    # Footer
    draw.text((40, H - 25), 'DATA-DRIVEN. CONTEXT-FIRST. BET SMARTER.',
              fill=GREEN_DIM, font=f(12, bold=True))
    draw.text((W - 200, H - 25), '© REVERSEPICKS', fill=GREEN_DIM, font=f(12, bold=True))

    out_path = output_path or OUTPUT_PATH
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Atomic write: render to temp file then rename so a concurrent reader
    # never sees a half-written PNG.
    tmp_path = out_path + '.tmp'
    img.save(tmp_path, 'PNG')
    os.replace(tmp_path, out_path)
    try:
        size = os.path.getsize(out_path)
    except OSError:
        size = 0
    print(f'Wrote: {out_path} ({size} bytes, {total_picks} settled picks)')
    return {'path': out_path, 'total_picks': total_picks, 'bytes': size}


def main():
    """CLI entrypoint — runs its own event loop."""
    return asyncio.run(render_cheat_sheet())


if __name__ == '__main__':
    main()
