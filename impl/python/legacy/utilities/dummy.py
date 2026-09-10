"""Contains dummy display class that displays the frame in a window."""

import pygame
from utilities.display import Display, Frame

class DummyDisplay(Display):
    """Display that displays the frame in a window."""

    def __init__(self, scalar=50):
        self._scalar = scalar
        if not pygame.get_init():
            pygame.init()
            self._screen = pygame.display.set_mode((9 * self._scalar, 17 * self._scalar))
            self._screen.fill((0, 0, 0))

    def makeframe(self):
        return Frame()

    def send(self, frame):
        for i in range(0, frame.nrows()):
            row = frame.row(i)

            for j in range(0, frame.ncols()):
                color = row[j]
                pixel = pygame.Rect(j * self._scalar, i * self._scalar, self._scalar, self._scalar)
                pygame.draw.rect(self._screen, (color.r, color.g, color.b), pixel)
        pygame.display.flip()
