""""Contains InputManager class that handles user input from keyboard and gamepad."""

import pygame
from time import sleep

class InputManager:
    """Manages user input from keyboard and gamepad."""
    def __init__(self, defaultBindings = {}):
        self._bindings = defaultBindings
        self._joysticks = {}
        if not pygame.get_init():
            pygame.init()
            self._screen = pygame.display.set_mode()
            self._screen.fill((0, 0, 0))
        pygame.joystick.init()
        self._joysticks = [pygame.joystick.Joystick(x) for x in range(pygame.joystick.get_count())]
        print(f"Initialized {len(self._joysticks)} joysticks")

    def bind(self, action, key):
        """Bind an action to a key."""
        self._bindings[action] = key

    def active_bind(self, action):
        raise NotImplementedError("Active binds are not implemented yet")

    def process_events(self):
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.KEYDOWN or event.type == pygame.KEYUP:
                if event.key in self._bindings:
                    self._bindings[event.key](event.type == pygame.KEYDOWN)
            elif event.type == pygame.JOYBUTTONDOWN or event.type == pygame.JOYBUTTONUP:
                if event.button in self._bindings:
                    self._bindings[event.button](event.type == pygame.JOYBUTTONDOWN)
            elif event.type == pygame.JOYAXISMOTION:
                if event.axis in self._bindings:
                    self._bindings[event.type](event.axis, event.value)
            elif event.type == pygame.JOYDEVICEADDED:
                joy = pygame.joystick.Joystick(event.device_index)
                self.add_joystick(joy)
            elif event.type == pygame.JOYDEVICEREMOVED:
                self.remove_joystick()

    def add_joystick(self, joy):
        joy.init()
        self._joysticks = [pygame.joystick.Joystick(x) for x in range(pygame.joystick.get_count())]
        print(f"Joystick {joy.get_name()} initialized")

    def remove_joystick(self):
        self._joysticks = [pygame.joystick.Joystick(x) for x in range(pygame.joystick.get_count())]
