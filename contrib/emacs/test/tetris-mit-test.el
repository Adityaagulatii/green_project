;;; tetris-mit-test.el --- ERT suite for tetris-mit.el  -*- lexical-binding: t; -*-

;;; Commentary:

;; Run from the repository root:
;;
;;   emacs --batch -Q -L contrib/emacs -l ert \
;;     -l contrib/emacs/test/tetris-mit-test.el -f ert-run-tests-batch-and-exit
;;
;; The integration tests start "python -m tetris_sim.server" on
;; 127.0.0.1, on a free port.  They use Python from $TETRIS_MIT_PYTHON,
;; else the project venv, else python3, and are skipped without one.

;;; Code:

(require 'ert)
(require 'cl-lib)
(require 'tetris-mit)

(defconst tetris-mit-test--black-digest
  "e0ee29ce7978a33861e6e63545deda9e734ea784ee8e4ba6fd6aa56b775f6ca9"
  "SPEC Appendix A: the digest of the all-black frame.")

(defconst tetris-mit-test--white-digest
  "cc1c8c603a0863247abc4b8a117a234714b37d2be10ca27218301e5e617830d8"
  "SPEC Appendix A: the digest of the all-white frame.")

(defun tetris-mit-test--rows (&optional fill)
  "A fresh 17x9 frame with every cell a copy of FILL (default black)."
  (let ((rows (make-vector tetris-mit-rows nil)))
    (dotimes (r tetris-mit-rows)
      (let ((row (make-vector tetris-mit-cols nil)))
        (dotimes (c tetris-mit-cols)
          (aset row c (copy-sequence (or fill [0 0 0]))))
        (aset rows r row)))
    rows))

(defun tetris-mit-test--set (rows r c rgb)
  "Set cell R, C of ROWS to RGB, and return ROWS."
  (aset (aref rows r) c rgb)
  rows)

(defun tetris-mit-test--lit (rows)
  "The number of cells of ROWS that are not black."
  (cl-loop for row across rows
           sum (cl-loop for cell across row count (not (equal cell [0 0 0])))))

(defun tetris-mit-test--error-code (thunk)
  "The protocol error code THUNK signals, or nil."
  (condition-case err
      (progn (funcall thunk) nil)
    (tetris-mit-protocol-error (nth 1 err))))

(defun tetris-mit-test--bg (r c)
  "The background of cell R, C in the rendered grid of the current buffer."
  (save-excursion
    (goto-char (point-min))
    (forward-line r)
    (forward-char (* c (length tetris-mit-cell-string)))
    (plist-get (get-text-property (point) 'face) :background)))

(defun tetris-mit-test--trace (name)
  "The path of the sealed trace NAME (read in place, never copied)."
  (expand-file-name (concat name ".json") (tetris-mit-trace-directory)))

;;;; Codec

(ert-deftest tetris-mit-test-encode-event ()
  (should (equal (tetris-mit-encode (tetris-mit-make-event "left" t))
                 "{\"type\":\"event\",\"action\":\"left\",\"down\":true}\n"))
  (should (equal (tetris-mit-encode (tetris-mit-make-event "hold" nil))
                 "{\"type\":\"event\",\"action\":\"hold\",\"down\":false}\n"))
  (should-error (tetris-mit-make-event "jump" t)))

(ert-deftest tetris-mit-test-roundtrip ()
  (let ((rows (tetris-mit-test--set (tetris-mit-test--rows) 3 4 [255 170 0])))
    (dolist (msg (list (tetris-mit-make-hello "controller" '((seed . 42)))
                       (tetris-mit-make-event "rotate_180" t)
                       (tetris-mit-make-event "soft_drop" nil)
                       (tetris-mit-make-tick 30)
                       (tetris-mit-make-frame 12 rows)
                       (tetris-mit-make-state 1200 3 4)
                       (tetris-mit-make-ping 7)
                       '((type . "error") (code . "bad_event") (message . "no"))))
      (let ((line (tetris-mit-encode msg)))
        (should (string-suffix-p "\n" line))
        (should (= 1 (cl-count ?\n line)))
        (should (equal (tetris-mit-decode line) msg))))))

(ert-deftest tetris-mit-test-roundtrip-random-frames ()
  (random "tetris-mit")
  (dotimes (_ 25)
    (let ((rows (tetris-mit-test--rows)))
      (dotimes (r tetris-mit-rows)
        (dotimes (c tetris-mit-cols)
          (tetris-mit-test--set rows r c (vector (random 256) (random 256)
                                                 (random 256)))))
      (let ((msg (tetris-mit-make-frame 1 rows)))
        (should (equal (tetris-mit-decode (tetris-mit-encode msg)) msg))))))

(defun tetris-mit-test--frame-line (rows &optional digest)
  "A frame message line for ROWS, with DIGEST if non-nil."
  (tetris-mit-encode `((type . "frame") (frame_no . 0) (rows . ,rows)
                       ,@(and digest `((digest . ,digest))))))

(ert-deftest tetris-mit-test-decode-rejects-bad-input ()
  (let* ((ok (tetris-mit-test--rows))
         (short (vconcat (cl-subseq ok 0 16)))
         (narrow (let ((r (tetris-mit-test--rows)))
                   (aset r 4 (vconcat (cl-subseq (aref r 4) 0 8))) r))
         (hot (tetris-mit-test--set (tetris-mit-test--rows) 0 0 [256 0 0]))
         (frac (tetris-mit-test--set (tetris-mit-test--rows) 5 5 [0 1.5 0]))
         (pair (tetris-mit-test--set (tetris-mit-test--rows) 9 1 [0 0]))
         (cases
          `(("not json" . "malformed") ("[1,2]" . "malformed") ("{}" . "malformed")
            ("\"x\"" . "malformed") ("{\"type\":5}" . "malformed")
            ("{\"type\":\"teleport\"}" . "unknown_type")
            ("{\"type\":\"hello\",\"protocol\":\"17x9-tetris-remote\",\"version\":9,\"role\":\"server\"}"
             . "version")
            ("{\"type\":\"event\",\"action\":\"jump\",\"down\":true}" . "bad_event")
            ("{\"type\":\"state\",\"score\":-1,\"level\":0,\"lines\":0}" . "bad_state")
            (,(tetris-mit-test--frame-line short) . "bad_frame")
            (,(tetris-mit-test--frame-line narrow) . "bad_frame")
            (,(tetris-mit-test--frame-line hot) . "bad_frame")
            (,(tetris-mit-test--frame-line frac) . "bad_frame")
            (,(tetris-mit-test--frame-line pair) . "bad_frame")
            (,(tetris-mit-test--frame-line ok tetris-mit-test--white-digest) . "digest")
            (,(concat "{\"type\":\"ping\",\"id\":\"" (make-string 70000 ?x) "\"}")
             . "too_large"))))
    (pcase-dolist (`(,line . ,code) cases)
      (should (equal (list (substring line 0 (min 60 (length line))) code)
                     (list (substring line 0 (min 60 (length line)))
                           (tetris-mit-test--error-code
                            (lambda () (tetris-mit-decode line)))))))
    (let ((tetris-mit-verify-digests nil))
      (should (tetris-mit-decode (tetris-mit-test--frame-line
                                  ok tetris-mit-test--white-digest))))))

(ert-deftest tetris-mit-test-digest-known-answers ()
  (should (equal (tetris-mit-frame-digest (tetris-mit-test--rows))
                 tetris-mit-test--black-digest))
  (should (equal (tetris-mit-frame-digest (tetris-mit-test--rows [255 255 255]))
                 tetris-mit-test--white-digest))
  (let ((bytes (tetris-mit-frame-bytes (tetris-mit-test--rows [255 170 0]))))
    (should (= (length bytes) 459))
    (should-not (multibyte-string-p bytes))
    (should (equal (substring bytes 0 3) (unibyte-string 255 170 0)))))

;;;; Rendering and keys

(ert-deftest tetris-mit-test-render-frame ()
  (let ((rows (tetris-mit-test--rows)))
    (tetris-mit-test--set rows 0 0 [255 0 0])
    (tetris-mit-test--set rows 16 8 [0 255 255])
    (tetris-mit-test--set rows 8 4 [153 0 255])
    (with-temp-buffer
      (tetris-mit-render-frame rows "Score 100  Level 1  Lines 2")
      (should (= (count-lines (point-min) (point-max)) 19))
      (goto-char (point-min))
      (dotimes (_ tetris-mit-rows)
        (should (= (- (line-end-position) (line-beginning-position))
                   (* tetris-mit-cols (length tetris-mit-cell-string))))
        (forward-line 1))
      (should (equal (tetris-mit-test--bg 0 0) "#ff0000"))
      (should (equal (tetris-mit-test--bg 16 8) "#00ffff"))
      (should (equal (tetris-mit-test--bg 8 4) "#9900ff"))
      (should (equal (tetris-mit-test--bg 8 3) "#000000"))
      (should (string-match-p "^Score 100  Level 1  Lines 2$" (buffer-string))))))

(ert-deftest tetris-mit-test-status-line ()
  (should (equal (tetris-mit-status-string
                  '((type . "state") (score . 300) (level . 2) (lines . 1)
                    (high_score . 900) (phase . "playing"))
                  120 "lockstep clock, seed 7")
                 "Score 300  Level 2  Lines 1  High 900  [playing]  frame 120  (lockstep clock, seed 7)"))
  (should (equal (tetris-mit-status-string nil) "Score 0  Level 0  Lines 0")))

(ert-deftest tetris-mit-test-key-bindings ()
  (dolist (binding tetris-mit-remote-bindings)
    (should (eq (lookup-key tetris-mit-remote-mode-map (kbd (car binding)))
                (tetris-mit--action-command (cdr binding)))))
  (pcase-dolist (`(,key . ,action)
                 '(("<left>" . "left") ("<right>" . "right") ("<down>" . "soft_drop")
                   ("SPC" . "hard_drop") ("<up>" . "rotate_cw") ("c" . "rotate_cw")
                   ("z" . "rotate_ccw") ("x" . "rotate_180") ("TAB" . "hold")
                   ("S-SPC" . "hold")))
    (should (eq (lookup-key tetris-mit-remote-mode-map (kbd key))
                (tetris-mit--action-command action))))
  (should (equal (sort (delete-dups (mapcar #'cdr tetris-mit-remote-bindings)) #'string<)
                 (sort (copy-sequence tetris-mit-actions) #'string<))))

(ert-deftest tetris-mit-test-key-sends-press-then-release ()
  (with-temp-buffer
    (tetris-mit-remote-mode)
    (let (sent)
      (cl-letf (((symbol-function 'tetris-mit-send)
                 (lambda (_proc &rest msgs) (setq sent (append sent msgs)))))
        (dolist (binding tetris-mit-remote-bindings)
          (setq sent nil)
          (call-interactively (key-binding (kbd (car binding))))
          (should (equal sent (list (tetris-mit-make-event (cdr binding) t)
                                    (tetris-mit-make-event (cdr binding) nil)))))))))

;;;; KAV plumbing

(ert-deftest tetris-mit-test-kav-id-and-schedule ()
  (should (equal (tetris-mit-kav-id "/x/08-hold.json") "KAV-08"))
  (should (equal (tetris-mit-kav-id "/x/14-bot-marathon.json") "KAV-14"))
  (should (equal (tetris-mit--kav-schedule
                  [[0 "left" t] [0 "left" :json-false] [5 "hold" t]] 8)
                 '((([0 "left" t] [0 "left" :json-false]) . 5) (([5 "hold" t]) . 3))))
  (should (equal (tetris-mit--kav-schedule [] 8000)
                 '((nil . 3600) (nil . 3600) (nil . 800)))))

;;;; Local mode: tetris.el on 9x17

(defconst tetris-mit-test--spec-shapes
  '(("I" ("...." "####" "...." "....") ("..#." "..#." "..#." "..#.")
     ("...." "...." "####" "....") (".#.." ".#.." ".#.." ".#.."))
    ("J" ("...." ".#.." ".###" "....") ("...." "..##" "..#." "..#.")
     ("...." "...." ".###" "...#") ("...." "..#." "..#." ".##."))
    ("L" ("...." "...#" ".###" "....") ("...." "..#." "..#." "..##")
     ("...." "...." ".###" ".#..") ("...." ".##." "..#." "..#."))
    ("O" ("...." ".##." ".##." "....") ("...." "...." ".##." ".##.")
     ("...." "...." "##.." "##..") ("...." "##.." "##.." "...."))
    ("S" ("...." "..##" ".##." "....") ("...." "..#." "..##" "...#")
     ("...." "...." "..##" ".##.") ("...." ".#.." ".##." "..#."))
    ("Z" ("...." ".##." "..##" "....") ("...." "...#" "..##" "..#.")
     ("...." "...." ".##." "..##") ("...." "..#." ".##." ".#.."))
    ("T" ("...." "..#." ".###" "....") ("...." "..#." "..##" "..#.")
     ("...." "...." ".###" "..#.") ("...." "..#." ".##." "..#.")))
  "SPEC §4.2 rotation states, top to bottom, as 4x4 boxes.")

(defun tetris-mit-test--normalize (cells)
  "CELLS, a list of (X . Y), translated to the origin and sorted."
  (let ((x0 (apply #'min (mapcar #'car cells)))
        (y0 (apply #'min (mapcar #'cdr cells))))
    (sort (mapcar (lambda (c) (cons (- (car c) x0) (- (cdr c) y0))) cells)
          (lambda (a b) (or (< (cdr a) (cdr b))
                            (and (= (cdr a) (cdr b)) (< (car a) (car b))))))))

(defun tetris-mit-test--box-cells (box)
  "The (X . Y) cells marked # in the 4x4 BOX."
  (cl-loop for line in box for y from 0
           nconc (cl-loop for ch across line for x from 0
                          when (eq ch ?#) collect (cons x y))))

(ert-deftest tetris-mit-test-local-shape-codes-match-spec-geometry ()
  "The index-to-piece map is read off tetris.el's own block coordinates."
  (dotimes (i 7)
    (let* ((cells (tetris-mit-test--normalize
                   (mapcar (lambda (v) (cons (aref v 0) (aref v 1)))
                           (append (aref (aref tetris-shapes i) 0) nil))))
           (matches (cl-loop for (code . boxes) in tetris-mit-test--spec-shapes
                             when (cl-some (lambda (box)
                                             (equal cells (tetris-mit-test--normalize
                                                           (tetris-mit-test--box-cells box))))
                                           boxes)
                             collect code)))
      (should (equal (list i matches)
                     (list i (list (aref tetris-mit-local-shape-codes i))))))))

(ert-deftest tetris-mit-test-local-colors-are-the-spec-palette ()
  (let ((x-colors (tetris-mit-local-x-colors))
        (tty-colors (tetris-mit-local-tty-colors)))
    (dotimes (i 7)
      (let ((hex (apply #'format "#%02x%02x%02x"
                        (tetris-mit-palette-rgb (aref tetris-mit-local-shape-codes i)))))
        (should (equal (gamegrid-color (aref x-colors i) 1.0) hex))
        (should (equal (aref tty-colors i) hex))))))

(defmacro tetris-mit-test--with-local (&rest body)
  "Run BODY in a fresh `tetris-mit-local' buffer with its timer stopped."
  (declare (indent 0))
  `(let ((buffer (save-window-excursion (tetris-mit-local))))
     (unwind-protect
         (with-current-buffer buffer
           (gamegrid-kill-timer)
           ,@body)
       (when (buffer-live-p buffer)
         (with-current-buffer buffer
           (gamegrid-kill-timer)
           (tetris-mit-local-mirror-stop))
         (kill-buffer buffer)))))

(ert-deftest tetris-mit-test-local-board-is-9x17 ()
  (let ((global-width (default-value 'tetris-width))
        (global-height (default-value 'tetris-height)))
    (tetris-mit-test--with-local
      (should (= tetris-width 9))
      (should (= tetris-height 17))
      (should (local-variable-p 'tetris-width))
      (should (= (default-value 'tetris-width) global-width))
      (should (= (default-value 'tetris-height) global-height))
      (should (= tetris-next-x 15))
      (let* ((frame (tetris-mit-local-frame))
             (rgb (apply #'vector (tetris-mit-palette-rgb
                                   (aref tetris-mit-local-shape-codes tetris-shape)))))
        (should (tetris-mit-valid-rows-p frame))
        (should (= 4 (tetris-mit-test--lit frame)))
        (cl-loop for row across frame
                 do (cl-loop for cell across row
                             unless (equal cell [0 0 0])
                             do (should (equal cell rgb)))))
      (dotimes (_ 12) (tetris-move-right))
      (let ((frame (tetris-mit-local-frame)))
        (should (cl-some (lambda (r) (not (equal (aref (aref frame r) 8) [0 0 0])))
                         (number-sequence 0 16))))
      (tetris-move-bottom)
      (let ((frame (tetris-mit-local-frame)))
        (should (= 8 (tetris-mit-test--lit frame)))
        (should (cl-some (lambda (c) (not (equal (aref (aref frame 16) c) [0 0 0])))
                         (number-sequence 0 8)))))))

;;;; Integration: the Python server on localhost

(defun tetris-mit-test--python ()
  "A Python for the server, or nil."
  (cl-find-if (lambda (p) (and p (or (file-executable-p p) (executable-find p))))
              (list (getenv "TETRIS_MIT_PYTHON") "/scratch/venvs/tetris-py/bin/python"
                    "python3")))

(defmacro tetris-mit-test--with-python (&rest body)
  "Run BODY with `tetris-mit-python' set, or skip the test."
  (declare (indent 0))
  `(let ((tetris-mit-python (or (tetris-mit-test--python)
                                (ert-skip "no Python for the server"))))
     ,@body))

(defmacro tetris-mit-test--with-server (spec &rest body)
  "SPEC is (SERVER PORT ARGS...): start a server with ARGS, and run BODY."
  (declare (indent 1))
  `(tetris-mit-test--with-python
     (let* ((,(car spec) (tetris-mit-start-server ,@(cddr spec)))
            (,(cadr spec) (cdr ,(car spec))))
       (unwind-protect (progn ,@body)
         (tetris-mit-stop-server ,(car spec))))))

(defun tetris-mit-test--engine-digests (seed frames events)
  "Digests of a direct run of the Python engine, via its conformance driver."
  (let ((file (make-temp-file "tetris-mit-run" nil ".json")))
    (unwind-protect
        (let ((process-environment
               (cons (concat "PYTHONPATH="
                             (expand-file-name "impl/python/engine" tetris-mit-root))
                     process-environment)))
          (with-temp-file file
            (insert (json-encode `((format . "17x9-tetris-trace") (spec_version . 1)
                                   (name . "emacs") (seed . ,seed) (frames . ,frames)
                                   (digest_every . 1)
                                   (events . ,(vconcat (mapcar #'vconcat events)))))))
          (with-temp-buffer
            (should (= 0 (call-process tetris-mit-python nil '(t nil) nil
                                       "-m" "tetris_engine.conformance" file)))
            (append (alist-get 'digests (tetris-mit--json-parse (buffer-string))) nil)))
      (delete-file file))))

(ert-deftest tetris-mit-test-remote-lockstep-matches-engine ()
  "Keys, frames and digests through the server equal a direct engine run."
  (tetris-mit-test--with-server (server port "--mode" "engine" "--clock" "lockstep"
                                        "--seed" "7")
    (let* ((digests nil)
           (tetris-mit-remote-frame-functions
            (list (lambda (msg) (push (alist-get 'digest msg) digests))))
           (buffer (save-window-excursion (tetris-mit-remote "127.0.0.1" port))))
      (unwind-protect
          (with-current-buffer buffer
            (should (tetris-mit--wait-until (lambda () tetris-mit--server) 15))
            (should (equal (alist-get 'mode tetris-mit--server) "engine"))
            (tetris-mit-remote-tick 91)
            (should (tetris-mit--wait-until (lambda () (eql tetris-mit--frame-no 90)) 15))
            (call-interactively (key-binding (kbd "SPC")))
            (call-interactively (key-binding (kbd "<left>")))
            (tetris-mit-remote-tick 60)
            (should (tetris-mit--wait-until (lambda () (eql tetris-mit--frame-no 150)) 15))
            (setq digests (nreverse digests))
            (should (= (length digests) 151))
            (should (equal digests
                           (tetris-mit-test--engine-digests
                            7 151 '((91 "hard_drop" t) (91 "hard_drop" :json-false)
                                    (91 "left" t) (91 "left" :json-false)))))
            (should (equal tetris-mit--drawn-digest (car (last digests))))
            (dotimes (r tetris-mit-rows)
              (dotimes (c tetris-mit-cols)
                (should (equal (tetris-mit-test--bg r c)
                               (tetris-mit-rgb-hex (aref (aref tetris-mit--rows r) c))))))
            (should (string-match-p "^Score [0-9]+  Level [0-9]+  Lines [0-9]+  High [0-9]+  \\[playing\\]  frame 150"
                                    (buffer-string))))
        (kill-buffer buffer)))))

(ert-deftest tetris-mit-test-remote-realtime-streams ()
  (tetris-mit-test--with-server (server port "--mode" "engine" "--seed" "1")
    (let ((buffer (save-window-excursion (tetris-mit-remote "127.0.0.1" port))))
      (unwind-protect
          (with-current-buffer buffer
            (should (tetris-mit--wait-until
                     (lambda () (and tetris-mit--frame-no (>= tetris-mit--frame-no 15)))
                     15))
            (should (equal (alist-get 'phase tetris-mit--state) "countdown"))
            (should (string-match-p "\\[countdown\\]" (buffer-string)))
            ;; Every countdown digit lights display row 3, column 4.
            (should (equal (tetris-mit-test--bg 3 4) "#ffffff"))
            (should (equal (tetris-mit-test--bg 0 0) "#000000"))
            (tetris-mit-remote-hard-drop)
            (tetris-mit-remote-disconnect)
            (should-not tetris-mit--process))
        (kill-buffer buffer)))))

(ert-deftest tetris-mit-test-local-mirror-to-display-server ()
  "tetris-mit-local sends valid frames that the display server accepts."
  (let ((html (make-temp-file "tetris-mit-wall" nil ".html")))
    (unwind-protect
        (tetris-mit-test--with-python
          (let* ((server (tetris-mit-start-server "--mode" "display" "--html" html
                                                  "--once"))
                 (output nil))
            (unwind-protect
                (tetris-mit-test--with-local
                  (tetris-mit-local-mirror-start "127.0.0.1" (cdr server))
                  (should (tetris-mit--wait-until
                           (lambda () (and tetris-mit--mirror-server
                                           (>= tetris-mit--mirror-frame-no 1)))
                           15))
                  (should (equal (alist-get 'mode tetris-mit--mirror-server) "display"))
                  (tetris-move-left)
                  (should (tetris-mit--wait-until
                           (lambda () (>= tetris-mit--mirror-frame-no 2)) 15))
                  (tetris-move-bottom)
                  (should (tetris-mit--wait-until
                           (lambda () (>= tetris-mit--mirror-frame-no 3)) 15))
                  (let ((sent tetris-mit--mirror-frame-no))
                    (tetris-mit-local-mirror-stop)
                    (should-not (advice-member-p #'tetris-mit--mirror-after-set-cell
                                                 'gamegrid-set-cell))
                    (should (tetris-mit--wait-until
                             (lambda () (not (process-live-p (car server)))) 20))
                    (setq output (tetris-mit-stop-server server))
                    (should (null tetris-mit--mirror-errors))
                    (should (string-match-p (format "%d frames shown" sent) output))))
              (tetris-mit-stop-server server))
            (should (> (file-attribute-size (file-attributes html)) 1000))))
      (delete-file html))))

(ert-deftest tetris-mit-test-kav-replay-private-server ()
  "Three KAVs replay through a private lockstep server, and pass."
  (tetris-mit-test--with-python
    (with-temp-buffer
      (let ((results (tetris-mit-kav-replay
                      (mapcar #'tetris-mit-test--trace
                              '("07-hard-drop" "08-hold" "13-suspended-input"))
                      nil nil (current-buffer))))
        (should (equal (mapcar (lambda (r) (plist-get r :line)) results)
                       '("KAV-07: 121/121 digests match — PASS"
                         "KAV-08: 131/131 digests match — PASS"
                         "KAV-13: 431/431 digests match — PASS")))
        (should (string-match-p "^KAV-08: 131/131 digests match — PASS$"
                                (buffer-string)))))))

(ert-deftest tetris-mit-test-kav-replay-existing-server ()
  "KAVs replay one after another against one running server."
  (tetris-mit-test--with-server (server port "--mode" "engine" "--clock" "lockstep")
    (let ((results (tetris-mit-kav-replay
                    (mapcar #'tetris-mit-test--trace
                            '("09-line-clear-single" "12-game-over-reset"))
                    "127.0.0.1" port)))
      (should (cl-every (lambda (r) (plist-get r :pass)) results))
      (should (equal (mapcar (lambda (r) (plist-get r :id)) results)
                     '("KAV-09" "KAV-12"))))))

(ert-deftest tetris-mit-test-kav-detects-a-wrong-digest ()
  "Verify the verifier: one flipped digest is a FAIL."
  (tetris-mit-test--with-server (server port "--mode" "engine" "--clock" "lockstep")
    (let* ((trace (tetris-mit-read-trace (tetris-mit-test--trace "07-hard-drop")))
           (digests (alist-get 'digests trace))
           (file (make-temp-file "07-corrupted" nil ".json")))
      (unwind-protect
          (progn
            (aset digests 60 (concat (if (eq (aref (aref digests 60) 0) ?0) "1" "0")
                                     (substring (aref digests 60) 1)))
            (with-temp-file file (insert (json-encode trace)))
            (let ((result (tetris-mit-kav-run file "127.0.0.1" port)))
              (should-not (plist-get result :pass))
              (should (equal (plist-get result :line)
                             (format "%s: 120/121 digests match — FAIL"
                                     (tetris-mit-kav-id file))))))
        (delete-file file)))))

(ert-deftest tetris-mit-test-kav-needs-lockstep ()
  (tetris-mit-test--with-server (server port "--mode" "engine")
    (let ((result (tetris-mit-kav-run (tetris-mit-test--trace "07-hard-drop")
                                      "127.0.0.1" port)))
      (should-not (plist-get result :pass))
      (should (string-match-p "needs --clock lockstep" (plist-get result :error))))))

(ert-deftest tetris-mit-test-kav-batch-entry-point ()
  "The CI entry point: emacs --batch -f tetris-mit-kav-batch TRACES."
  (tetris-mit-test--with-python
    (with-temp-buffer
      (let* ((process-environment (cons (concat "TETRIS_MIT_PYTHON=" tetris-mit-python)
                                        process-environment))
             (coding-system-for-read 'utf-8)
             (status (call-process
                      (expand-file-name invocation-name invocation-directory)
                      nil '(t nil) nil "--batch" "-Q"
                      "-l" (expand-file-name "contrib/emacs/tetris-mit.el" tetris-mit-root)
                      "-f" "tetris-mit-kav-batch"
                      (tetris-mit-test--trace "07-hard-drop")
                      (tetris-mit-test--trace "09-line-clear-single"))))
        (should (equal (list status (buffer-string))
                       (list 0 (concat "KAV-07: 121/121 digests match — PASS\n"
                                       "KAV-09: 118/118 digests match — PASS\n"
                                       "PASS: 2/2 KAVs pass\n"))))))))

(provide 'tetris-mit-test)

;;; tetris-mit-test.el ends here
