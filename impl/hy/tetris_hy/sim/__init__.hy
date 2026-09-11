"The headless building simulator for the Hy engine (SPEC §10).

- recorder.hy: the Display sink that records frames and input, and the
  Animation interface;
- building.hy: the facade model, with the provisional row → floor mapping;
- ansi.hy: the ANSI truecolor terminal renderer;
- bot.hy: a small deterministic auto-player;
- cli.hy: the command line, also run by `hy -m tetris_hy.sim`."

(import tetris_hy.sim.recorder [Recorder record tetris])
(import tetris_hy.sim.building [GREEN-BUILDING window windows lit-floors])
(import tetris_hy.sim.bot [make-bot plan])
