"""Building model: the 153 lit windows of MIT's Green Building (Building 54)
as a 17x9 display (SPEC §10.4).

The row->floor and column->bay mapping is TBD until it is confirmed at the
hack (Sep 13). The default below is PROVISIONAL: display row 0 is the top
lit floor (20), rows run down to floor 4, and column c is bay c+1 counted
left to right as seen from the viewing side.
"""

from dataclasses import dataclass

from tetris_engine.tables import COLS, ROWS


@dataclass(frozen=True)
class Window:
    row: int
    col: int
    floor: int
    bay: int


@dataclass(frozen=True)
class Building:
    stories: int = 21
    rows: int = ROWS
    cols: int = COLS
    top_floor: int = 20
    provisional: bool = True

    def __post_init__(self):
        if self.rows * self.cols != 153:
            raise ValueError("the facade has 153 windows")
        if not (self.rows <= self.top_floor <= self.stories):
            raise ValueError("lit floors must fit inside the tower")

    def window(self, row, col):
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise IndexError((row, col))
        return Window(row, col, self.top_floor - row, col + 1)

    def windows(self):
        return [self.window(r, c) for r in range(self.rows)
                for c in range(self.cols)]

    def lit_floors(self):
        return list(range(self.top_floor, self.top_floor - self.rows, -1))

    def as_dict(self):
        return {"stories": self.stories, "rows": self.rows, "cols": self.cols,
                "top_floor": self.top_floor, "provisional": self.provisional}
