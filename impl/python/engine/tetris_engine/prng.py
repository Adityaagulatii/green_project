"""Spec PRNG (xorshift32) and 7-bag Fisher-Yates shuffle (SPEC §9.3)."""

from .tables import BAG_ORDER

MASK32 = 0xFFFFFFFF
ZERO_SEED_STATE = 0x9E3779B9


def seed_state(seed):
    """Map an integer seed to a non-zero 32-bit xorshift state."""
    s = int(seed) & MASK32
    return s if s != 0 else ZERO_SEED_STATE


def xorshift32(x):
    """One step of Marsaglia's xorshift32 (13, 17, 5). Returns the new state,
    which is also the output value."""
    x ^= (x << 13) & MASK32
    x ^= x >> 17
    x ^= (x << 5) & MASK32
    return x


def shuffle_bag(state):
    """Return (bag, new_state): a Fisher-Yates shuffle of BAG_ORDER.

    for i = 6 down to 1: state = xorshift32(state); j = state mod (i + 1);
    swap(bag[i], bag[j]).
    """
    bag = list(BAG_ORDER)
    for i in range(len(bag) - 1, 0, -1):
        state = xorshift32(state)
        j = state % (i + 1)
        bag[i], bag[j] = bag[j], bag[i]
    return tuple(bag), state


def fisher_yates_inplace(items, state):
    """Shuffle a list in place with the spec procedure; return the new state.
    Used by the differential suite to force the legacy Bag onto spec order."""
    for i in range(len(items) - 1, 0, -1):
        state = xorshift32(state)
        j = state % (i + 1)
        items[i], items[j] = items[j], items[i]
    return state
