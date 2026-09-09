import os
import pygame
import numpy as np
import time
import keyboard
import tkinter as tk
import threading

from utilities.input_manager import InputManager
from utilities.display import Color, Frame
from utilities.dummy import DummyDisplay

# Defining game constants
FPS = 30 # Screen refresh rate (CANNOT BE MODIFIED)
CLASSIC_SIMULATION_RATE = 60 # Classic input handling rate (e.g. NES Tetris, Tetris 99)
MODERN_SIMULATION_RATE = 1000 # Modern input handling rate (e.g. Tetr.io)

# Calculate equivalent ns per frame and subtick for both simulation rates
NS_PER_FRAME = 1_000_000_000 // FPS
CLASSIC_NS_PER_SUBTICK = 1_000_000_000 // CLASSIC_SIMULATION_RATE
MODERN_NS_PER_SUBTICK = 1_000_000_000 // MODERN_SIMULATION_RATE

# Defining stanndard Tetromino colors
X = Color() # Black
W = Color(255, 255, 255) # White
C = Color(0, 255, 255) # Cyan
B = Color(0, 0, 255) # Blue
O = Color(255, 170, 0) # Orange
Y = Color(255, 255, 0) # Yellow
G = Color(0, 255, 0) # Green
P = Color(153, 0, 255) # Purple
R = Color(255, 0, 0) # Red
A = Color(42, 42, 42) # Gray

# Defining standard Tetromino shapes and rotations
SHAPES = {
    "I":
    [
        np.array([[X,X,X,X],
                  [C,C,C,C],
                  [X,X,X,X],
                  [X,X,X,X]]),
        np.array([[X,X,C,X],
                  [X,X,C,X],
                  [X,X,C,X],
                  [X,X,C,X]]),
        np.array([[X,X,X,X],
                  [X,X,X,X],
                  [C,C,C,C],
                  [X,X,X,X]]),
        np.array([[X,C,X,X],
                  [X,C,X,X],
                  [X,C,X,X],
                  [X,C,X,X]])
    ],
    "J":
    [
        np.array([[X,X,X,X],
                  [X,B,X,X],
                  [X,B,B,B],
                  [X,X,X,X]]),
        np.array([[X,X,X,X],
                  [X,X,B,B],
                  [X,X,B,X],
                  [X,X,B,X]]),
        np.array([[X,X,X,X],
                  [X,X,X,X],
                  [X,B,B,B],
                  [X,X,X,B]]),
        np.array([[X,X,X,X],
                  [X,X,B,X],
                  [X,X,B,X],
                  [X,B,B,X]])
    ],
    "L":
    [
        np.array([[X,X,X,X],
                  [X,X,X,O],
                  [X,O,O,O],
                  [X,X,X,X]]),
        np.array([[X,X,X,X],
                  [X,X,O,X],
                  [X,X,O,X],
                  [X,X,O,O]]),
        np.array([[X,X,X,X],
                  [X,X,X,X],
                  [X,O,O,O],
                  [X,O,X,X]]),
        np.array([[X,X,X,X],
                  [X,O,O,X],
                  [X,X,O,X],
                  [X,X,O,X]])
    ],
    "O":
    [
        np.array([[X,X,X,X],
                  [X,Y,Y,X],
                  [X,Y,Y,X],
                  [X,X,X,X]]),
        np.array([[X,X,X,X],
                  [X,X,X,X],
                  [X,Y,Y,X],
                  [X,Y,Y,X]]),
        np.array([[X,X,X,X],
                  [X,X,X,X],
                  [Y,Y,X,X],
                  [Y,Y,X,X]]),
        np.array([[X,X,X,X],
                  [Y,Y,X,X],
                  [Y,Y,X,X],
                  [X,X,X,X]]),
    ],
    "S":
    [
        np.array([[X,X,X,X],
                  [X,X,G,G],
                  [X,G,G,X],
                  [X,X,X,X]]),
        np.array([[X,X,X,X],
                  [X,X,G,X],
                  [X,X,G,G],
                  [X,X,X,G]]),
        np.array([[X,X,X,X],
                  [X,X,X,X],
                  [X,X,G,G],
                  [X,G,G,X]]),
        np.array([[X,X,X,X],
                  [X,G,X,X],
                  [X,G,G,X],
                  [X,X,G,X]])
    ],
    "Z":
    [
        np.array([[X,X,X,X],
                  [X,R,R,X],
                  [X,X,R,R],
                  [X,X,X,X]]),
        np.array([[X,X,X,X],
                  [X,X,X,R],
                  [X,X,R,R],
                  [X,X,R,X]]),
        np.array([[X,X,X,X],
                  [X,X,X,X],
                  [X,R,R,X],
                  [X,X,R,R]]),
        np.array([[X,X,X,X],
                  [X,X,R,X],
                  [X,R,R,X],
                  [X,R,X,X]])
    ],
    "T":
    [
        np.array([[X,X,X,X],
                  [X,X,P,X],
                  [X,P,P,P],
                  [X,X,X,X]]),
        np.array([[X,X,X,X],
                  [X,X,P,X],
                  [X,X,P,P],
                  [X,X,P,X]]),
        np.array([[X,X,X,X],
                  [X,X,X,X],
                  [X,P,P,P],
                  [X,X,P,X]]),
        np.array([[X,X,X,X],
                  [X,X,P,X],
                  [X,P,P,X],
                  [X,X,P,X]])
    ],
}

# Defining wall kick data for Arika SRS. Source: https://tetris.fandom.com/wiki/Super_Rotation_System
#TODO: Implement wall kicks
KICK_TABLE ={
    "J": [
            [[0,0], [0,0], [0,0], [0,0], [0,0]],
            [[ 0, 0], [ 1, 0], [ 1,-1], [ 0, 2], [ 1, 2]],
            [[ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0]],
            [[ 0, 0], [-1, 0], [-1,-1], [ 0, 2], [-1, 2]]
        ],
    "L": [
            [[0,0], [0,0], [0,0], [0,0], [0,0]],
                        [[ 0, 0], [ 1, 0], [ 1,-1], [ 0, 2], [ 1, 2]],
                        [[ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0]],
                        [[ 0, 0], [-1, 0], [-1,-1], [ 0, 2], [-1, 2]]
        ],
    "S": [
            [[0,0], [0,0], [0,0], [0,0], [0,0]],
                        [[ 0, 0], [ 1, 0], [ 1,-1], [ 0, 2], [ 1, 2]],
                        [[ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0]],
                        [[ 0, 0], [-1, 0], [-1,-1], [ 0, 2], [-1, 2]]
        ],
    "T": [
            [[0,0], [0,0], [0,0], [0,0], [0,0]],
                        [[ 0, 0], [ 1, 0], [ 1,-1], [ 0, 2], [ 1, 2]],
                        [[ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0]],
                        [[ 0, 0], [-1, 0], [-1,-1], [ 0, 2], [-1, 2]]
        ],
    "Z": [
            [[0,0], [0,0], [0,0], [0,0], [0,0]],
                        [[ 0, 0], [ 1, 0], [ 1,-1], [ 0, 2], [ 1, 2]],
                        [[ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0], [ 0, 0]],
                        [[ 0, 0], [-1, 0], [-1,-1], [ 0, 2], [-1, 2]]
        ],
    "I": [
            [[0,0], [-1,0], [2,0], [-1,0], [2,0]],
            [[0, 0], [1, 0], [1,0], [1, 1], [1, -2]],
            [[ 0, 0], [2, 0], [-1, 0], [2, -1], [-1, -1]],
            [[0, 0], [0, 0], [0, 0], [0, -2], [0, 1]]
        ],
    "O": [
            [[0,0],[0,0],[0,0],[0,0],[0,0]],
            [[0,-1],[0,-1],[0,-1],[0,-1],[0,-1]],
            [[-1,-1],[-1,-1],[-1,-1],[-1,-1],[-1,-1]],
            [[-1,0],[-1,0],[-1,0],[-1,0],[-1,0]]
     ]
}

#it was really hard to find any information about how 180 degree rotations work with SRS+ (tetr.io system) so i lowk
#just guessed so it's definitely not perfect but who cares
KICK_TABLE_180 ={
    "J": [
            [[0,0], [0,1], [1,0], [-1,0], [0,-1]],
            [[0,0], [1,0], [0,1], [0,-1], [-1,0]],
            [[0,0], [0,0], [0,0], [0,0], [0,0]],
            [[0,0], [0,0], [0,0], [0,0], [0,0]]
        ],
    "L": [
            [[0,0], [0,1], [1,0], [-1,0], [0,-1]],
                        [[0,0], [1,0], [0,1], [0,-1], [-1,0]],
                        [[0,0], [0,0], [0,0], [0,0], [0,0]],
                        [[0,0], [0,0], [0,0], [0,0], [0,0]]
        ],
    "S": [
            [[0,0], [0,1], [1,0], [-1,0], [0,-1]],
                        [[0,0], [1,0], [0,1], [0,-1], [-1,0]],
                        [[0,0], [0,0], [0,0], [0,0], [0,0]],
                        [[0,0], [0,0], [0,0], [0,0], [0,0]]
        ],
    "T": [
            [[0,0], [0,1], [1,0], [-1,0], [0,-1]],
                        [[0,0], [1,0], [0,1], [0,-1], [-1,0]],
                        [[0,0], [0,0], [0,0], [0,0], [0,0]],
                        [[0,0], [0,0], [0,0], [0,0], [0,0]]
        ],
    "Z": [
            [[0,0], [0,1], [1,0], [-1,0], [0,-1]],
                        [[0,0], [1,0], [0,1], [0,-1], [-1,0]],
                        [[0,0], [0,0], [0,0], [0,0], [0,0]],
                        [[0,0], [0,0], [0,0], [0,0], [0,0]]
        ],
    "I": [
            [[0,0], [0,0], [-1,0], [2,0], [-1,1]],
            [[0, 0], [0, 0], [0,1], [0, -2], [1, 1]],
            [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0]],
            [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0]]
        ],
    "O": [
            [[0,0],[0,0],[0,0],[0,0],[0,0]],
            [[0,-1],[0,-1],[0,-1],[0,-1],[0,-1]],
            [[-1,-1],[-1,-1],[-1,-1],[-1,-1],[-1,-1]],
            [[-1,0],[-1,0],[-1,0],[-1,0],[-1,0]]
     ]
}

# Defining gravity values for each level.
GRAVITY = [1/48, 1/43, 1/38, 1/33, 1/28, 1/23, 1/18, 1/13, 1/8, 1/6,
           1/5, 1/5, 1/5, 1/4, 1/4, 1/4, 1/3, 1/3, 1/3, 1/2,
           1/2, 1/2, 1/2, 1/2, 1/2, 1/2, 1/2, 1/2, 1/2, 1]

# Countdown at the start of the game
COUNTDOWN = {
    "3": np.array([
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,X,X,X,W,W,X,X],
        [X,X,X,X,X,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,X,X,X,W,W,X,X],
        [X,X,X,X,X,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
    ]),

    "2": np.array([
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,X,X,X,W,W,X,X],
        [X,X,X,X,X,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,W,W,X,X,X,X,X],
        [X,X,W,W,X,X,X,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,W,W,W,W,W,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
    ]),

    "1": np.array([
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,W,W,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
        [X,X,X,X,X,X,X,X,X],
    ]),
}

class Tetromino:
    """Represents a Tetromino piece in the game."""
    def __init__(self, shape):
        self._shape = shape
        self._states = SHAPES[shape]
        self._position = [0, 3] # in padded background coordinates
        self._rotation = 0
        self._lockDelay = 30
        self._lockResets = 15
        self._landed = False

    def getState(self, offset=0):
        return self._states[(self._rotation + offset) % len(self._states)]

    def getPosition(self, offset=[0,0]):
        return [self._position[0] + offset[0], self._position[1] + offset[1]]

    def setPosition(self, position):
        self._position = position

    def setLanded(self, state):
        self._landed = state

    def isLanded(self):
        return self._landed

    def setLockDelay(self, increment):
        self._lockDelay -= increment

    def resetLockDelay(self):
        self._lockDelay = 30

    def getLockDelay(self):
        return self._lockDelay

    def rotateCW(self):
        oldRotation = self._rotation
        self._rotation = (self._rotation + 1) % len(self._states)
        self._lockDelay = 30
        return (oldRotation, self._rotation)

    def rotateCCW(self):
        oldRotation = self._rotation
        self._rotation = (self._rotation - 1) % len(self._states)
        self._lockDelay = 30
        return (oldRotation, self._rotation)

    def moveLeft(self):
        self._position[1] -= 1
        self._lockDelay = 30

    def moveRight(self):
        self._position[1] += 1
        self._lockDelay = 30


    def moveDown(self):
        self._position[0] += 1
        self._lockDelay = 30

    def lock(self): #TODO: Might be able to remove
        self._lockDelay = 0

    def __str__(self):
        return f"Shape: {self._shape}, Rotation: {self._rotation}"

class Bag:
    """Implements the 7-bag randomizer for Tetromino generation."""
    def __init__(self):
        self._bag = []
        self._index = 0

        self._newBag()

    def _newBag(self):
        """Generate a new randomized bag of Tetrominoes."""
        self._bag = list(SHAPES.keys())
        np.random.shuffle(self._bag)
        self._index = 0

    def getTetromino(self):
        """Get the next Tetromino from the bag. If the bag is empty,
        generate a new randomized bag then get the next Tetromino.
        """

        if self._index >= len(self._bag):
            self._newBag()
            self._index = 0
        shape = self._bag[self._index]
        self._index += 1
        return Tetromino(shape)

GLOBAL_STATE = {
    "HIGH_SCORE": 0,
    "CURRENT_SCORE": 0,
    "HOLD_PIECE": None,
}

class Tetris:
    def __init__(self, display=None, modern=True, ghostPiece=True):
        # Defining standard keybinds
        KEYBINDS = {
            # Keyboard bindings
            pygame.K_UP: self._rotateCW,
            pygame.K_x: self._rotate180,
            pygame.K_LCTRL: self._rotateCCW,
            pygame.K_z: self._rotateCCW,
            pygame.K_SPACE: self._hardDrop,
            pygame.K_DOWN: self._softDrop,
            pygame.K_LEFT: self._moveLeft,
            pygame.K_RIGHT: self._moveRight,
            pygame.K_LSHIFT: self._hold,
            pygame.K_c: self._rotateCW,

            # Controller bindings
            pygame.CONTROLLER_BUTTON_DPAD_UP: self._hardDrop,
            pygame.CONTROLLER_BUTTON_DPAD_DOWN: self._softDrop,
            pygame.CONTROLLER_BUTTON_DPAD_LEFT: self._moveLeft,
            pygame.CONTROLLER_BUTTON_DPAD_RIGHT: self._moveRight,
            pygame.JOYAXISMOTION: self._processJoystickAxis,
            pygame.CONTROLLER_BUTTON_A: self._rotateCW,
            pygame.CONTROLLER_BUTTON_B: self._rotateCCW,
            pygame.CONTROLLER_BUTTON_LEFTSHOULDER: self._hold,
            pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: self._hold,
            pygame.CONTROLLER_BUTTON_Y: self._hold
        }

        # Handling settings
        HANDLING = {
            "ARR": 2, # Auto Repeat Rate (frames)
            "DAS": 3, # Delayed Auto Shift (frames)
            "DCD": 2, # DAS Cut Delay (frames)
            "SDF": 6, # Soft Drop Factor (multiplier to gravity)
        }

        self._display = display if display is not None else DummyDisplay()
        self._displayFrame = self._display.makeframe()

        # Create background with white border padding around the active frame for collision detection and piece spawning
        self._background = Frame(rows=self._displayFrame.nrows()+3, cols=self._displayFrame.ncols()+2)
        for i in range(self._background.nrows()):
            for j in range(self._background.ncols()):
                if i == 0 or j == 0 or j == self._background.ncols()-1 or i == self._background.nrows()-1 or i == self._background.nrows()-2:
                    self._background.row(i)[j] = W

        self._foreground = Frame(rows=self._displayFrame.nrows()+3, cols=self._displayFrame.ncols()+2)

        self._DCD = HANDLING["DCD"]
        
        self._doCountdown()
        self._resetGame()

        # Set subtick processing rate
        if modern:
            self._subtickRate = MODERN_SIMULATION_RATE
        else:
            self._subtickRate = CLASSIC_SIMULATION_RATE

        self._ghostPiece = ghostPiece
        self._inputManager = InputManager(KEYBINDS)
        self._highScore = 0

        pygame.key.set_repeat(NS_PER_FRAME // 1_000_000 * HANDLING["DAS"], NS_PER_FRAME // 1_000_000 * HANDLING["ARR"])

    def play(self):
        """Main game loop."""
        fractionalPosition = 0

        # TODO: Change to hyprid if, process, sleep, while structure with ns
        while self._playing:
            frameTime = time.perf_counter()
            fractionalPosition += self._gravity * 2 #Running at half normal FPS
            self._DCDCounter += 2

            self.time -= 1/FPS

            while time.perf_counter() - frameTime < 1/FPS:
                simTime = time.perf_counter()
                self._inputManager.process_events()

                while time.perf_counter() - simTime < 1/self._subtickRate:
                    pass # busy wait until next subtick

            self._checkLevelUp() # Check for level up every frame to update gravity if necessary

            #TODO: Fix gravity implementation
            if fractionalPosition >= 1:
                self._moveDown(True)
                fractionalPosition = 0

            self._updateDisplayFrame()

            #TODO: Change to single background thread instead of starting a new thread every frame
            # threading.Thread(target=self._display.send, args=(self._displayFrame,)).start()

            self._display.send(self._displayFrame) #non threaded for testing

        self._display.send(Frame()) # Set display to black
        pygame.display.quit()
        pygame.quit()


    def _maskTetromino(self):
        '''Mask the active Tetromino onto the foreground frame.'''
        position = self._activeTetromino.getPosition()
        state = self._activeTetromino.getState()
        for i in range(state.shape[0]):
            for j in range(state.shape[1]):
                if state[i][j] != X:
                    self._foreground[position[0]+i][position[1]+j] = state[i][j]

    def _updateDisplayFrame(self):
        '''Update the display frame by masking the foreground frame onto the display frame.'''
        self._foreground[:] = self._background[:]
        if self._ghostPiece:
            self._ghostTetromino()
        self._maskTetromino()
        self._displayFrame[:] = self._foreground[1:self._displayFrame.nrows()+1, 1:self._displayFrame.ncols()+1]

    def _checkCollision(self, position, state):
        '''Check if the given position and state of the active Tetromino would collide with the background.'''
        for i in range(state.shape[0]):
            for j in range(state.shape[1]):
                if state[i][j] != X and self._background[position[0]+i][position[1]+j] != X:
                    return True
        return False

    def _rotateCW(self, eventDown):
        '''Rotate the active Tetromino clockwise.'''
        if eventDown and self._CWavailable:
            self._CWavailable = False
            if self._DCDCounter >= self._DCD:
                for i in range(5):
                    srsoffsetx=KICK_TABLE[self._activeTetromino._shape][self._activeTetromino._rotation][i][0]-KICK_TABLE[self._activeTetromino._shape][(self._activeTetromino._rotation+1)%4][i][0]
                    srsoffsety=KICK_TABLE[self._activeTetromino._shape][self._activeTetromino._rotation][i][1]-KICK_TABLE[self._activeTetromino._shape][(self._activeTetromino._rotation+1)%4][i][1]
                    pos = [self._activeTetromino.getPosition()[0]-srsoffsety,self._activeTetromino.getPosition()[1]+srsoffsetx]
                    state = self._activeTetromino.getState(offset=1)
                    if not self._checkCollision(pos, state):
                        self._activeTetromino.rotateCW()
                        self._activeTetromino._position=pos
                        self._DCDCounter = 0
                        break
        elif not eventDown and not self._CWavailable:
            self._CWavailable = True


    def _rotateCCW(self, eventDown):
        '''Rotate the active Tetromino counterclockwise.'''
        if eventDown and self._CCWavailable:
            self._CCWavailable = False
            if self._DCDCounter >= self._DCD:
                for i in range(5):
                    srsoffsetx=KICK_TABLE[self._activeTetromino._shape][self._activeTetromino._rotation][i][0]-KICK_TABLE[self._activeTetromino._shape][(self._activeTetromino._rotation-1)%4][i][0]
                    srsoffsety=KICK_TABLE[self._activeTetromino._shape][self._activeTetromino._rotation][i][1]-KICK_TABLE[self._activeTetromino._shape][(self._activeTetromino._rotation-1)%4][i][1]
                    pos = [self._activeTetromino.getPosition()[0]-srsoffsety,self._activeTetromino.getPosition()[1]+srsoffsetx]
                    state = self._activeTetromino.getState(offset=-1)
                    if not self._checkCollision(pos, state):
                        self._activeTetromino.rotateCCW()
                        self._activeTetromino._position=pos
                        self._DCDCounter = 0
                        break
        elif not eventDown and not self._CCWavailable:
            self._CCWavailable = True
            self._DCDCounter = 0 #why do the other two rotation scripts not have this line...?

    def _rotate180(self, eventDown):
        '''Rotate the active Tetromino 180 degrees.'''
        if eventDown and self._180available:
            self._180available = False
            if self._DCDCounter >= self._DCD:
                for i in range(5):
                    srsoffsetx=KICK_TABLE_180[self._activeTetromino._shape][self._activeTetromino._rotation][i][0]-KICK_TABLE[self._activeTetromino._shape][(self._activeTetromino._rotation+2)%4][i][0]
                    srsoffsety=KICK_TABLE_180[self._activeTetromino._shape][self._activeTetromino._rotation][i][1]-KICK_TABLE[self._activeTetromino._shape][(self._activeTetromino._rotation+2)%4][i][1]
                    pos = [self._activeTetromino.getPosition()[0]-srsoffsety,self._activeTetromino.getPosition()[1]+srsoffsetx]
                    state = self._activeTetromino.getState(offset=2)
                    if not self._checkCollision(pos, state):
                        self._activeTetromino.rotateCCW()
                        self._activeTetromino.rotateCCW()
                        self._activeTetromino._position=pos
                        self._DCDCounter = 0
                        break
        elif not eventDown and not self._180available:
            self._180available = True

    def _moveLeft(self, eventDown):
        '''Move the active Tetromino left.'''
        if eventDown:
            pos = self._activeTetromino.getPosition(offset=[0,-1])
            state = self._activeTetromino.getState()
            if not self._checkCollision(pos, state):
                self._activeTetromino.moveLeft()

    def _moveRight(self, eventDown):
        '''Move the active Tetromino right.'''
        if eventDown:
            pos = self._activeTetromino.getPosition(offset=[0,1])
            state = self._activeTetromino.getState()
            if not self._checkCollision(pos, state):
                self._activeTetromino.moveRight()

    def _moveDown(self, eventDown):
        '''Move the active Tetromino down.'''
        if eventDown:
            pos = self._activeTetromino.getPosition()
            state = self._activeTetromino.getState()
            newpos = [pos[0] + 1, pos[1]]
            if not self._checkCollision(newpos, state):
                self._activeTetromino.setLanded(False)
                self._activeTetromino.moveDown()
                return

            # Collision detected: lock the tetromino into the background and spawn next piece
            # TODO: implement lock delay and lock resets
            for i in range(state.shape[0]):
                for j in range(state.shape[1]):
                    if state[i][j] != X:
                        self._background[pos[0]+i][pos[1]+j] = state[i][j]
            self._checkForClears()
            self._activeTetromino = self._bag.getTetromino()
            self._holdAvailable = True
            if self._checkCollision(self._activeTetromino.getPosition(), self._activeTetromino.getState()):
                self._gameOver()

    def _softDrop(self, eventDown):
        '''Soft drop the active Tetromino.'''
        if eventDown:
            self._moveDown(eventDown)

    def _hardDrop(self, eventDown):
        '''Hard drop the active Tetromino.'''
        if eventDown and self._hardDropAvailable:
            # Move down until collision then lock into background
            self._hardDropAvailable = False
            pos = self._activeTetromino.getPosition()
            state = self._activeTetromino.getState()
            if self._DCDCounter >= self._DCD:
                while True:
                    newpos = [pos[0] + 1, pos[1]]
                    if self._checkCollision(newpos, state):
                        # lock into background
                        for i in range(state.shape[0]):
                            for j in range(state.shape[1]):
                                if state[i][j] != X:
                                    self._background[pos[0]+i][pos[1]+j] = state[i][j]
                        self._checkForClears()
                        self._activeTetromino = self._bag.getTetromino()
                        if self._checkCollision(self._activeTetromino.getPosition(), self._activeTetromino.getState()):
                            self._gameOver()
                        self._holdAvailable = True
                        self._DCDCounter = 0
                        break
                    self._holdAvailable = True # Allow hold after hard drop
                    self._activeTetromino.moveDown()
                    pos = self._activeTetromino.getPosition()
        elif not eventDown and not self._hardDropAvailable:
            self._hardDropAvailable = True
            self._DCDCounter = 0

    def _ghostTetromino(self):
        pos = self._activeTetromino.getPosition()
        state = self._activeTetromino.getState()
        ghostPos = pos.copy()
        while True:
            newGhostPos = [ghostPos[0] + 1, ghostPos[1]]
            if self._checkCollision(newGhostPos, state):
                break
            ghostPos = newGhostPos
        for i in range(state.shape[0]):
            for j in range(state.shape[1]):
                if state[i][j] != X:
                    self._foreground[ghostPos[0]+i][ghostPos[1]+j] = A

    def _processJoystickAxis(self, axis, value):
        if axis == 0:
            if value < -0.5:
                self._moveLeft(True)
            elif value > 0.5:
                self._moveRight(True)
        elif axis == 1:
            if value < -0.5:
                self._hardDrop(True)
            elif value > 0.5:
                self._softDrop(True)

    def _hold(self, eventDown):
        '''Hold the active Tetromino.'''
        global GLOBAL_STATE

        if not self._holdAvailable:
            return
        if eventDown:
            if self._holdTetromino is None:
                self._holdTetromino = self._activeTetromino
                self._activeTetromino = self._bag.getTetromino()
            else:
                self._holdTetromino, self._activeTetromino = self._activeTetromino, self._holdTetromino
            GLOBAL_STATE["HOLD_PIECE"] = self._holdTetromino
            self._holdTetromino.setPosition([0, 3])
            self._holdAvailable = False

    def _checkForClears(self, rows: list[int] = [1,18]):
        '''Check for and clear any completed lines.'''
        global GLOBAL_STATE

        clear = False
        for i in range(rows[0], rows[1]):
            if all(self._background.row(i)[j] != X for j in range(1, self._background.ncols()-1)):
                clear = True
                for j in range(1, self._background.ncols()-1):
                    self._background.row(i)[j] = W
        if clear:
            frameTime = time.perf_counter()
            self._displayFrame[:] = self._background[1:self._displayFrame.nrows()+1, 1:self._displayFrame.ncols()+1]
            self._display.send(self._displayFrame)
            while time.perf_counter() - frameTime < 5/FPS:
                pass
            for i in range(rows[0], rows[1]):
                if all(self._background.row(i)[j] == W for j in range(1, self._background.ncols()-1)):
                    # Clear line and move everything above it down
                    for j in range(1, self._background.ncols()-1):
                        self._background.row(i)[j] = X
                    for k in range(i-1, 0, -1):
                        for j in range(1, self._background.ncols()-1):
                            self._background.row(k+1)[j] = self._background.row(k)[j]
                    for j in range(1, self._background.ncols()-1):
                        self._background.row(1)[j] = X
                    self._linesCleared += 1
                    self.score += 100 * (self._linesCleared // 10 + 1)
            GLOBAL_STATE["CURRENT_SCORE"] = self.score

    def _checkLevelUp(self):
        '''Check if the player has leveled up and increase gravity if so.'''
        # lineTarget = max(self._level * 10 + 10, max(100, self._level * 10 - 50))
        lineTarget = min(self._level + 5, max(100, self._level * 5 - 50)) # Faster level progression for testing
        if lineTarget <= self._linesCleared:
            self._level += 1
            self._linesCleared = self._linesCleared - lineTarget
            self._gravity = GRAVITY[min(self._level, len(GRAVITY)-1)]

    def _fillUp(self):
        '''Fill the background after game over.'''
        for i in reversed(range(1, self._background.nrows()-2)):
            for j in range(1, self._background.ncols()-1):
                self._background.row(i)[j] = W
            frameTime = time.perf_counter()
            self._displayFrame[:] = self._background[1:self._displayFrame.nrows()+1, 1:self._displayFrame.ncols()+1]
            self._display.send(self._displayFrame)
            while time.perf_counter() - frameTime < 2/FPS:
                pass

        print("Waiting for quit")
        for i in range(5):
                print('.', end='')
                self._display.send(self._displayFrame)
                time.sleep(1)
                if keyboard.is_pressed('q'):
                    print("Quitting Tetris")
                    self._playing = False

    def _fallDown(self):
        '''Make the background fall down after game over.'''
        for i in range(1, self._background.nrows()-2):
            for j in range(1, self._background.ncols()-1):
                self._background.row(i)[j] = X
            frameTime = time.perf_counter()
            self._displayFrame[:] = self._background[1:self._displayFrame.nrows()+1, 1:self._displayFrame.ncols()+1]
            self._display.send(self._displayFrame)
            while time.perf_counter() - frameTime < 2/FPS:
                pass

    def _gameOver(self):
        '''Print score and reset game.'''
        global GLOBAL_STATE

        self._highScore = max(self._highScore, self.score)
        GLOBAL_STATE["HIGH_SCORE"] = self._highScore
        print(f"Level: {self._level}, Lines Cleared: {self._linesCleared}, Score: {self.score}, High Score: {self._highScore}")
        self._fillUp()

        self._fallDown()

        self._background = Frame(rows=self._displayFrame.nrows()+3, cols=self._displayFrame.ncols()+2)
        for i in range(self._background.nrows()):
            for j in range(self._background.ncols()):
                if i == 0 or j == 0 or j == self._background.ncols()-1 or i == self._background.nrows()-1 or i == self._background.nrows()-2:
                    self._background.row(i)[j] = W

        self._foreground = Frame(rows=self._displayFrame.nrows()+3, cols=self._displayFrame.ncols()+2)

        self._resetGlobal()
        self._doCountdown()
        self._resetGame()

    def _doCountdown(self):
        for count in ("3", "2", "1"):
            f1 = Frame()
            f1._array = COUNTDOWN[count]
            self._display.send(f1)
            time.sleep(1)

        self._display.send(Frame())

    def _resetGlobal(self):
        global GLOBAL_STATE
        GLOBAL_STATE["CURRENT_SCORE"] = 0
        GLOBAL_STATE["HOLD_PIECE"] = None 

    def _resetGame(self):
        pygame.event.clear()
        self._resetGlobal()
        self._bag = Bag()
        self._activeTetromino = self._bag.getTetromino()
        self._holdTetromino = None
        self._holdAvailable = True
        self._level = 0 # Start at level 0 for CPW
        self._gravity = GRAVITY[self._level]
        self._linesCleared = 0
        self.score = 0
        self.time = 300
        self._CWavailable = True
        self._CCWavailable = True
        self._180available = True
        self._hardDropAvailable = True
        self._DCDCounter = self._DCD
        self._playing = True

def second_screen():
    global GLOBAL_STATE 

    root = tk.Tk()
    root.title("Tetris")
    root.geometry("500x600")
    root.configure(bg="black")

    label_score = tk.Label(root, text="0", font=("Arial", 24))
    label_score.pack(padx=20, pady=20)

    cell_size = 100
    canvas_size = 4*cell_size
    canvas = tk.Canvas(root, width=canvas_size, height=canvas_size, bg="black", highlightthickness=0)
    cells = dict()
    colors = dict()
    canvas.pack(padx=8, pady=8)

    label_high_score = tk.Label(root, text="0", font=("Arial", 24))
    label_high_score.pack(padx=20, pady=20)

    def empty_canvas():
        for r in range(4):
            for c in range(4):
                x0 = c*cell_size
                y0 = r*cell_size
                rect = canvas.create_rectangle(x0,y0,x0+cell_size,y0+cell_size, fill="black")
                cells[(r,c)] = rect
                colors[(r,c)] = "black"

    def update_canvas(hold_piece):
        piece_colors = hold_piece.getState()
        for r in range(4):
            for c in range(4):
                col = piece_colors[r][c]
                x0 = c*cell_size
                y0 = r*cell_size
                colors[(r,c)] = f"#{col.r:02x}{col.g:02x}{col.b:02x}"
                canvas.itemconfigure(cells[(r,c)], fill=colors[(r, c)])

    empty_canvas()

    def update_label():
        label_high_score.config(text=f"High Score: {GLOBAL_STATE['HIGH_SCORE']}")
        label_score.config(text=f"Score: {GLOBAL_STATE['CURRENT_SCORE']}")
        if GLOBAL_STATE["HOLD_PIECE"] is not None:
            # breakpoint()
            update_canvas(GLOBAL_STATE["HOLD_PIECE"])
        else:
            empty_canvas()


        root.after(100, update_label)

    root.after(100, update_label)
    root.mainloop()

if __name__ == "__main__":
    # threading.Thread(target=second_screen, daemon=True).start()

    tetris = Tetris()
    tetris.play()
