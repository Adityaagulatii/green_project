"""Property-based tests for SPEC.md §11, P1-P18 (P19 is in test_kat.py).

Trajectories mix the demo bot (to reach clears, levels and game overs)
with Hypothesis-generated events at random frames: presses without
releases, releases without presses, all eight actions.
"""

import copy
from dataclasses import replace

from hypothesis import assume, given
from hypothesis import strategies as st

from tetris_engine import ACTIONS, PALETTE, core, render, render_codes
from tetris_engine import tables as T
from tetris_engine.core import (CLEARING, COUNTDOWN, EMPTY_BOARD, GAMEOVER,
                                PLAYING, Held, Piece, apply_action, collides,
                                drop_row)
from tetris_engine.frame import codes_digest
from tetris_engine.prng import seed_state
from tetris_sim.bot import Bot

PALETTE_SET = set(PALETTE.values())
CADENCE = {48: 25, 43: 22, 38: 20, 33: 17, 28: 15, 23: 12, 18: 9, 13: 7,
           8: 4, 6: 3, 5: 3, 4: 2, 3: 2, 2: 1, 1: 1}
ROTATIONS = ("rotate_cw", "rotate_ccw", "rotate_180")
TAP = lambda a: [(a, True), (a, False)]  # noqa: E731

seeds = st.integers(0, 2 ** 32 - 1)
events = st.lists(st.tuples(st.sampled_from(ACTIONS), st.booleans()),
                  min_size=1, max_size=4)


@st.composite
def schedules(draw, max_frames=260):
    n = draw(st.integers(1, max_frames))
    sched = draw(st.dictionaries(st.integers(0, n - 1), events, max_size=40))
    return n, sched, draw(st.booleans())


def booted(seed):
    s = core.new_game(seed)
    for _ in range(91):
        s = core.step(s)
    return s


def trajectory(seed, n, sched, bot, boot=True):
    s = booted(seed) if boot else core.new_game(seed)
    b = Bot() if bot else None
    for k in range(n):
        evs = (list(b(s)) if b else []) + list(sched.get(k, []))
        nxt = core.step(s, evs)
        yield s, evs, nxt
        s = nxt


def is_reset(prev, nxt):
    return prev.phase == GAMEOVER and nxt.phase == COUNTDOWN


def cells(p):
    return [(p.row + i, p.col + j) for i, j in T.CELLS[p.shape][p.rot]]


def playing(seed):
    return replace(booted(seed), phase=PLAYING, boot=False, timer=0)


# ------------------------------------------------ constructed states

@st.composite
def boards(draw, almost_full=False):
    h = draw(st.integers(0, 12))
    rows = []
    for _ in range(h):
        color = draw(st.sampled_from(T.BAG_ORDER))
        if almost_full and draw(st.booleans()):
            hole = draw(st.integers(0, 8))
            row = "".join("." if c == hole else color for c in range(9))
        else:
            mask = draw(st.integers(0, 510))  # never 511: no full rows
            row = "".join(color if mask >> c & 1 else "." for c in range(9))
        rows.append("W" + row + "W")
    empty = "W" + "." * 9 + "W"
    return (("W" * 11,) + (empty,) * (17 - h) + tuple(rows)
            + ("W" * 11,) * 2)


@st.composite
def placed(draw, almost_full=False):
    board = draw(boards(almost_full))
    shape = draw(st.sampled_from(T.BAG_ORDER))
    rot = draw(st.integers(0, 3))
    row = draw(st.integers(-1, 17))
    col = draw(st.integers(-1, 9))
    assume(not collides(board, shape, rot, row, col))
    return board, Piece(shape, rot, row, col)


# ------------------------------------------------ P1 .. P18

@given(seeds, schedules())
def test_p01_frame_contract(seed, sch):
    n, sched, bot = sch
    for _, _, nxt in trajectory(seed, n, sched, bot, boot=False):
        frame = render(nxt)
        assert len(frame) == 17 and all(len(row) == 9 for row in frame)
        for row in frame:
            for rgb in row:
                assert all(isinstance(v, int) and 0 <= v <= 255 for v in rgb)
                assert rgb in PALETTE_SET


@given(seeds, schedules(120))
def test_p02_purity_and_determinism(seed, sch):
    n, sched, bot = sch
    digests = []
    for prev, evs, nxt in trajectory(seed, n, sched, bot):
        snapshot = copy.deepcopy(prev)
        again = core.step(prev, evs)
        assert prev == snapshot and again == nxt
        digests.append(codes_digest(render_codes(nxt)))
    replay = [codes_digest(render_codes(x))
              for _, _, x in trajectory(seed, n, sched, bot)]
    assert replay == digests


@given(seeds, schedules())
def test_p03_board_frame(seed, sch):
    n, sched, bot = sch
    for _, _, s in trajectory(seed, n, sched, bot):
        b = s.board
        assert len(b) == 20 and all(len(r) == 11 for r in b)
        assert b[18] == b[19] == "W" * 11
        assert all(r[0] == "W" and r[10] == "W" for r in b)
        assert "." not in b[0]


@given(seeds, schedules())
def test_p04_legal_active_piece(seed, sch):
    n, sched, bot = sch
    for _, _, s in trajectory(seed, n, sched, bot):
        if s.phase != PLAYING:
            continue
        for r, c in cells(s.active):
            assert 0 <= r < 20 and 0 <= c < 11
        if collides(s.board, *s.active):
            assert not s.hold_available  # only a hold can place it there


@given(seeds, schedules())
def test_p05_no_full_rows_at_rest(seed, sch):
    n, sched, bot = sch
    for _, _, s in trajectory(seed, n, sched, bot):
        for r in range(1, 18):
            assert "." in s.board[r][1:10]


@given(seeds)
def test_p06_seven_bag(seed):
    s = core.new_game(seed)
    drawn = [s.active.shape]
    for _ in range(69):
        piece, rng, bag, idx = core._draw(s)
        s = replace(s, rng=rng, bag=bag, bag_index=idx)
        drawn.append(piece.shape)
    for k in range(0, 70, 7):
        assert sorted(drawn[k:k + 7]) == sorted(T.BAG_ORDER)


@given(seeds, schedules())
def test_p06_bag_state_and_fresh_bag_per_game(seed, sch):
    n, sched, bot = sch
    for prev, _, s in trajectory(seed, n, sched, bot):
        assert sorted(s.bag) == sorted(T.BAG_ORDER)
        assert 1 <= s.bag_index <= 7
        if is_reset(prev, s):
            assert s.bag_index == 1 and s.active.shape == s.bag[0]


@given(seeds, st.integers(0, 40))
def test_p07_gravity_cadence(seed, level):
    s = replace(booted(seed), level=level, acc=0.0)
    n = CADENCE[T.GRAVITY_DEN[min(level, 29)]]
    spawns, row, since, drops = s.spawns, s.active.row, 0, 0
    while drops < 4:
        s = core.step(s)
        since += 1
        if s.spawns != spawns:
            break  # landed and locked
        if s.active.row != row:
            assert s.active.row == row + 1 and since == n
            row, since, drops = s.active.row, 0, drops + 1
    assert drops >= 1


@given(seeds, placed())
def test_p08_hard_drop_lands_on_ghost(seed, bp):
    board, piece = bp
    s = replace(booted(seed), board=board, active=piece, dcd=0)
    shown = render_codes(replace(s, phase=PLAYING))
    g = drop_row(board, piece)
    target = [(g + i, piece.col + j) for i, j in T.CELLS[piece.shape][piece.rot]]
    for r, c in target:
        if 1 <= r <= 17:
            assert shown[r - 1][c - 1] in ("G", piece.shape)
    nxt = core.step(s, [("hard_drop", True)])
    before_clear = nxt.flash if nxt.phase == CLEARING else nxt.board
    for r, c in target:
        assert before_clear[r][c] in (piece.shape, "W")
        if nxt.phase != CLEARING:
            assert before_clear[r][c] == piece.shape


@given(seeds, schedules())
def test_p09_ghost_geometry(seed, sch):
    n, sched, bot = sch
    for _, _, s in trajectory(seed, n, sched, bot):
        if s.phase != PLAYING:
            continue
        a = s.active
        codes = render_codes(s)
        shown = {(r, c) for r in range(17) for c in range(9)
                 if codes[r][c] == "G"}
        active = {(r - 1, c - 1) for r, c in cells(a)}
        g = drop_row(s.board, a)
        ghost = {(g + i - 1, a.col + j - 1) for i, j in T.CELLS[a.shape][a.rot]}
        visible = {(r, c) for r, c in ghost - active if 0 <= r < 17}
        assert shown == visible


@given(seeds, placed(), st.sampled_from([("left", "right"), ("right", "left")]))
def test_p10_shift_inverse(seed, bp, pair):
    board, piece = bp
    s = replace(playing(seed), board=board, active=piece)
    s1 = apply_action(s, pair[0], True)
    if s1.active != s.active:
        assert apply_action(s1, pair[1], True).active == s.active


gated_events = st.lists(st.tuples(
    st.sampled_from(ROTATIONS + ("hard_drop", "left", "right", "soft_drop")),
    st.booleans()), max_size=12)


@given(seeds, placed(), st.integers(0, 4), gated_events,
       st.tuples(st.booleans(), st.booleans(), st.booleans(), st.booleans()))
def test_p11_one_gated_action_per_logical_frame(seed, bp, dcd, evs, latches):
    board, piece = bp
    s = replace(playing(seed), board=board, active=piece, dcd=dcd + 2,
                cw_available=latches[0], ccw_available=latches[1],
                r180_available=latches[2], hard_available=latches[3])
    successes = 0
    for action, down in evs:
        before = s
        s = apply_action(s, action, down)
        if action in ROTATIONS and down and s.spawns == before.spawns \
                and s.active.rot != before.active.rot:
            successes += 1
        if action == "hard_drop" and down and s.spawns != before.spawns:
            successes += 1
        if s.phase == GAMEOVER:
            break
        if s.phase == CLEARING:  # resumption continues the same frame
            s = replace(s, phase=PLAYING, flash=None, pending_gameover=False)
    assert successes <= 1


@given(seeds, placed(), st.sampled_from(ROTATIONS + ("hard_drop",)))
def test_p12_latches(seed, bp, action):
    board, piece = bp
    s = replace(playing(seed), board=board, active=piece, dcd=10)
    s1 = apply_action(s, action, True)
    assume(s1.phase == PLAYING)
    assert apply_action(s1, action, True) == s1


@given(seeds, placed(almost_full=True), st.integers(0, 40))
def test_p13_scoring_formula(seed, bp, lines):
    board, piece = bp
    s = replace(booted(seed), board=board, active=piece, dcd=0, lines=lines,
                level=40)  # target 45 > lines: no level-up this frame
    nxt = core.step(s, [("hard_drop", True)])
    n = 0
    if nxt.phase == CLEARING:
        n = sum(1 for r in range(1, 18) if nxt.flash[r] == "W" * 11)
    assert nxt.score - s.score == sum(100 * ((lines + k) // 10 + 1)
                                      for k in range(1, n + 1))
    assert nxt.lines == lines + n


@given(seeds, schedules())
def test_p13_score_monotone_within_game(seed, sch):
    n, sched, bot = sch
    for prev, _, s in trajectory(seed, n, sched, bot):
        assert s.score % 100 == 0
        if is_reset(prev, s):
            continue
        assert s.score >= prev.score
        if s.score != prev.score:
            assert s.phase == CLEARING and prev.phase != CLEARING \
                or prev.phase == CLEARING and s.phase == CLEARING \
                and s.flash != prev.flash


@given(seeds, schedules())
def test_p14_levels(seed, sch):
    n, sched, bot = sch
    for prev, _, s in trajectory(seed, n, sched, bot):
        assert s.lines >= 0
        if not is_reset(prev, s):
            assert prev.level <= s.level <= prev.level + 1


@given(seeds, placed(), st.one_of(st.none(), st.tuples(
    st.sampled_from(T.BAG_ORDER), st.integers(0, 3))))
def test_p15_hold(seed, bp, held):
    board, piece = bp
    hold = None if held is None else Held(*held)
    s = replace(playing(seed), board=board, active=piece, hold=hold)
    s1 = apply_action(s, "hold", True)
    assert s1.hold == Held(piece.shape, piece.rot)
    if hold is not None:
        assert s1.active == Piece(hold.shape, hold.rot, 0, 3)
    else:
        assert s1.active.rot == 0 and (s1.active.row, s1.active.col) == (0, 3)
    assert not s1.hold_available
    assert apply_action(s1, "hold", True) == s1


@given(seeds, schedules())
def test_p15_one_hold_per_lock(seed, sch):
    n, sched, bot = sch
    for prev, _, s in trajectory(seed, n, sched, bot):
        # With hold used (so hold is non-empty) and no piece drawn in the
        # step, there was no lock, so no second hold may happen. A lock
        # followed by a hold within one step is legal (thorough run found
        # seed=0, sch=(2, {0: [hold], 1: [hold]}, bot)).
        if not prev.hold_available and not s.hold_available \
                and s.spawns == prev.spawns:
            assert s.hold == prev.hold


def top_out(seed, per_frame=None):
    """Hard drop every frame until the game-over sequence starts."""
    s = booted(seed)
    trail = [s]
    while s.phase not in (GAMEOVER,) and not (s.phase == CLEARING
                                               and s.pending_gameover):
        s = core.step(s, per_frame(s) if per_frame else TAP("hard_drop"))
        trail.append(s)
        assert len(trail) < 400
    return trail


def run_phases(s, limit=1000):
    phases = []
    while len(phases) < limit:
        s = core.step(s)
        phases.append(s.phase)
        if s.phase == PLAYING:
            break
    return s, phases


def test_p16_boot_lengths():
    s = core.new_game(1)
    frames = []
    for _ in range(91):
        s = core.step(s)
        frames.append(render_codes(s))
        assert s.phase == COUNTDOWN
    assert frames[:30] == [T.COUNTDOWN["3"]] * 30
    assert frames[30:60] == [T.COUNTDOWN["2"]] * 30
    assert frames[60:90] == [T.COUNTDOWN["1"]] * 30
    assert frames[90] == ("." * 9,) * 17
    assert core.step(s).phase == PLAYING


@given(seeds)
def test_p16_p17_game_over_sequence_and_reset(seed):
    trail = top_out(seed)
    s = trail[-1]
    score_at_over, old_high = s.score, trail[-2].high_score
    phases = [s.phase]
    s, rest = run_phases(s)
    phases += rest
    flash = phases.count(CLEARING)
    assert flash in (0, 5)
    assert phases.count(GAMEOVER) == 218
    assert phases.count(COUNTDOWN) == 90
    assert phases[-1] == PLAYING and phases == ([CLEARING] * flash
                                                 + [GAMEOVER] * 218
                                                 + [COUNTDOWN] * 90
                                                 + [PLAYING])
    # P17: replay to the first countdown state after the game over
    s = trail[-1]
    while s.phase != COUNTDOWN:
        s = core.step(s)
    assert (s.score, s.level, s.lines, s.hold) == (0, 0, 0, None)
    assert s.board == EMPTY_BOARD and s.bag_index == 1
    assert s.high_score == max(old_high, score_at_over)


@given(seeds, events)
def test_p16_flash_is_five_frames_and_p18_flash_input_queued(seed, xs):
    s = booted(seed)
    bot = Bot()
    for _ in range(2000):
        s = core.step(s, bot(s))
        if s.phase == CLEARING and s.timer == 5 and not s.pending_gameover:
            break
    assume(s.phase == CLEARING and not s.pending_gameover)
    entry = s
    # P16: exactly 5 frames show the flash
    t, shown = entry, [render_codes(entry)]
    while t.phase == CLEARING and len(shown) < 20:
        t = core.step(t)
        shown.append(render_codes(t))
    assert shown[:5] == [render_codes(entry)] * 5 and shown[5] != shown[0]
    # P18: events during the flash == the same events at the next frame
    a = core.step(entry, xs)
    while a.phase != PLAYING:
        a = core.step(a)
    a = core.step(a)
    b = entry
    while b.phase != PLAYING:
        b = core.step(b)
    b = core.step(b, xs)
    assert a == b


@given(seeds, st.integers(0, 330), events)
def test_p18_input_discarded_during_game_over_and_countdown(seed, offset, xs):
    s = top_out(seed)[-1]
    for _ in range(offset):
        if s.phase == PLAYING:
            break
        s = core.step(s)
    quiet = core.step(s)
    # a flash with a pending game over still queues input (SPEC §9.2)
    assume(s.phase in (GAMEOVER, COUNTDOWN)
           and quiet.phase in (GAMEOVER, COUNTDOWN))
    assert core.step(s, xs) == quiet


def test_p18_boot_input_discarded():
    s = core.new_game(3)
    for _ in range(91):
        assert core.step(s, TAP("hard_drop") + [("left", True)]) == core.step(s)
        s = core.step(s)


def test_seed_zero_is_well_defined():
    assert seed_state(0) != 0 and seed_state(2 ** 32) == seed_state(0)
