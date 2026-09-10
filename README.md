# 17×9 Tetris: polyglot fork

This fork rebuilds the game as Python → Hy → Clojure/ClojureScript → Guile 3 (see [`docs/POLYGLOT-PLAN.md`](docs/POLYGLOT-PLAN.md)).
The original code lives, unchanged, in [`impl/python/legacy/`](impl/python/legacy/).

## Original

The text below is the upstream README by Nevin Thinagar ([Nevin-Thinagar/17x9-Tetris](https://github.com/Nevin-Thinagar/17x9-Tetris)), kept verbatim apart from heading levels. Paths such as `tetris.py` and `utilities/` now live in `impl/python/legacy/`.

### 17 x 9 Tetris
Tetris! But on a 17 x 9 grid, what an odd choice...

This game was put together in a relatively short amount of time to hit a deadline for this project. It works decently and is still fun to play, but I think there is still room for improvement both aesthetically and in terms of gameplay. Anything in the `tetris.py` and `input_manager.py` file can be changed without impacting the overall functionality of the system. Things in the other utilities can be changed as well, but this will require restructuring other parts of the system and is not the prefered method of improving the game. However, if there is a significant improvement by restructuring those systems, go for it and make a pull request!

I'm hoping that by making this public, people can play around with it, see how the system works, and overall improve the whole system! Maybe you'll even be inspired to make your own game for this kind of display! (Please feel free to do this as well! You can just reuse the display and dummy classes)

#### Things that need to be fixed
- Game loop is not performant
- Better line clear and game over animations
- Auto-repeat inputs are cancelled when a different key is pressed
- More to be added as I think of them...

#### Things that need to be added
- Lock delay
- Color change on level up
- Second window with next pieces, hold piece, score, level, and timer
- More accurate scoring
- High score tracking (partially implemented, needs visual on second window)
- Pausing the game
- Change handling and keybinds while in-game
- Scroll score after game over
- More to be added as I think of them...

#### Other notes
- If you forked this repo before the most recent commit to fix the bug with the `_playing` variable not being initialized, you will have to pull again
- The `Display.send()` function should be called *at most* at 30 FPS (i.e. Once every 0.033 seconds). The send command might get smarter at some point to handle faster send commands, but currently it is up to the game to limit the frame rate

Feel free to add any other features that you think would be useful even if they're not listed here! Just make a PR :)

Disclaimer: *This is a personal educational project and is not affiliated with, sponsored by, or endorsed by The Tetris Company*
