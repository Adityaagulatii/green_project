;;; tetris-mit-display-test.el --- ERT suite for tetris-mit-display.el  -*- lexical-binding: t; -*-

;;; Commentary:

;; Run from the repository root, together with the main suite:
;;
;;   emacs --batch -Q -L contrib/emacs -L contrib/emacs/test -l ert \
;;     -l tetris-mit-test -l tetris-mit-display-test \
;;     -f ert-run-tests-batch-and-exit

;;; Code:

(require 'ert)
(require 'cl-lib)
(require 'tetris-mit-display)
(require 'tetris-mit-test)

(defmacro tetris-mit-display-test--fresh (&rest body)
  "Run BODY with a freshly built display buffer as the current buffer."
  (declare (indent 0))
  `(let ((buffer (tetris-mit-display-buffer)))
     (with-current-buffer buffer
       (tetris-mit-display-stop)
       (tetris-mit-display--init-grid)
       (setq tetris-mit-display--state nil tetris-mit-display--note nil))
     (unwind-protect (with-current-buffer buffer ,@body)
       (when (buffer-live-p buffer) (kill-buffer buffer)))))

(defun tetris-mit-display-test--face-at (r c)
  "The background of the overlay at row R, column C of the display buffer."
  (let ((overlay (aref tetris-mit-display--overlays (+ (* r tetris-mit-cols) c))))
    (plist-get (overlay-get overlay 'face) :background)))

;;;; Grid and rendering

(ert-deftest tetris-mit-display-test-grid-geometry ()
  "9 wide and 17 tall: 17 lines of 9 cells, row 0 at the top (SPEC §2.1)."
  (tetris-mit-display-test--fresh
    (should (= (length tetris-mit-display--overlays) 153))
    (should (= (length (overlays-in (point-min) (point-max))) 153))
    (dotimes (r tetris-mit-rows)
      (dotimes (c tetris-mit-cols)
        (let ((overlay (aref tetris-mit-display--overlays (+ (* r tetris-mit-cols) c)))
              (width (length tetris-mit-cell-string)))
          (save-excursion
            (goto-char (overlay-start overlay))
            (should (= (1- (line-number-at-pos)) r))
            (should (= (current-column) (* c width)))
            (should (= (- (overlay-end overlay) (overlay-start overlay)) width))))))
    (should (string-match-p "^Score 0  Level 0  Lines 0  (emacs-17x9)$"
                            (buffer-string)))))

(ert-deftest tetris-mit-display-test-diff-repaints-only-changes ()
  (tetris-mit-display-test--fresh
    (let ((black (tetris-mit-test--rows))
          (frame (tetris-mit-test--rows))
          (before (copy-sequence tetris-mit-display--overlays)))
      (should (= 0 (tetris-mit-display-show black 0)))
      (tetris-mit-test--set frame 0 0 [255 0 0])
      (tetris-mit-test--set frame 16 8 [0 255 255])
      (tetris-mit-test--set frame 8 4 [153 0 255])
      (should (= 3 (tetris-mit-display-show frame 1)))
      (should (equal (tetris-mit-display-test--face-at 0 0) "#ff0000"))
      (should (equal (tetris-mit-display-test--face-at 16 8) "#00ffff"))
      (should (equal (tetris-mit-display-test--face-at 8 4) "#9900ff"))
      (should (equal (tetris-mit-display-test--face-at 8 3) "#000000"))
      (should (= 0 (tetris-mit-display-show frame 2)))
      (tetris-mit-test--set frame 0 0 [0 0 0])
      (should (= 1 (tetris-mit-display-show frame 3)))
      (should (= tetris-mit-display--frames 4))
      (should (eql tetris-mit-display--frame-no 3))
      ;; the overlays are never recreated, and faces are shared per color
      (should (cl-every #'eq before tetris-mit-display--overlays))
      (should (eq (overlay-get (aref tetris-mit-display--overlays 0) 'face)
                  (overlay-get (aref tetris-mit-display--overlays 1) 'face)))
      (should (equal (tetris-mit-display-rows) frame))
      (should-error (tetris-mit-display-show (vconcat (cl-subseq frame 0 16))))
      (should-error (tetris-mit-display-show
                     (tetris-mit-test--set (tetris-mit-test--rows) 1 1 [0 256 0]))))))

(defun tetris-mit-display-test--show-loop (a b buffer n)
  "Show frames A and B alternately N times in BUFFER; return allocation deltas.
The value is (CONSES VECTOR-CELLS STRINGS), from `memory-use-counts'.
The test byte-compiles this function, so that the loop itself
allocates nothing."
  (let ((before (memory-use-counts)))
    (dotimes (k n)
      (tetris-mit-display-show (if (= 0 (% k 2)) a b) k nil buffer))
    (let ((after (memory-use-counts)))
      ;; (CONSES FLOATS VECTOR-CELLS SYMBOLS STRING-CHARS INTERVALS STRINGS)
      (list (- (nth 0 after) (nth 0 before))
            (- (nth 2 after) (nth 2 before))
            (- (nth 6 after) (nth 6 before))))))

(ert-deftest tetris-mit-display-test-no-allocation-per-frame ()
  "After warm-up, showing a frame allocates no conses, vectors or strings.
Each of the 200 frames repaints all 153 overlays."
  (dolist (fn '(tetris-mit-display-show tetris-mit-display--valid-p
                tetris-mit-display--face tetris-mit-display-test--show-loop))
    (unless (byte-code-function-p (symbol-function fn))
      (byte-compile fn)))
  (tetris-mit-display-test--fresh
    (let ((a (tetris-mit-test--rows))
          (b (tetris-mit-test--rows [255 170 0]))
          (buffer (current-buffer)))
      (tetris-mit-display-test--show-loop a b buffer 2)   ; warm the face cache
      ;; the warm-up ends on B, so switching to A repaints every overlay
      (should (= 153 (tetris-mit-display-show a 0 nil buffer)))
      (garbage-collect)
      (pcase-let ((`(,conses ,vector-cells ,strings)
                   (tetris-mit-display-test--show-loop a b buffer 200)))
        ;; memory-use-counts' own result list is the only allowance
        (should (< conses 20))
        (should (= vector-cells 0))
        (should (= strings 0))))))

(ert-deftest tetris-mit-display-test-status-line-follows-state ()
  (tetris-mit-display-test--fresh
    (tetris-mit-display-show (tetris-mit-test--rows) 5
                             '((score . 300) (level . 2) (lines . 1) (phase . "playing")))
    (should (string-match-p "^Score 300  Level 2  Lines 1  \\[playing\\]  (emacs-17x9)$"
                            (buffer-string)))
    (let ((tetris-mit-display-show-frame-number t))
      (tetris-mit-display-show (tetris-mit-test--rows) 6)
      (should (string-match-p "frame 6" (buffer-string))))))

(ert-deftest tetris-mit-display-test-provider ()
  "An in-process frame provider is polled at 30 FPS; nil means no new frame."
  (tetris-mit-display-test--fresh
    (let* ((frames (list (tetris-mit-test--rows [255 0 0])
                         `((rows . ,(tetris-mit-test--rows [0 255 0])) (frame_no . 1)
                           (state . ((score . 100) (level . 0) (lines . 1))))
                         nil
                         (tetris-mit-test--rows [0 0 255])))
           (calls 0))
      (tetris-mit-display-set-provider
       (lambda () (setq calls (1+ calls)) (pop frames)))
      (should (tetris-mit--wait-until (lambda () (>= calls 6)) 5))
      (tetris-mit-display--stop-provider)
      (should (= tetris-mit-display--frames 3))
      (should (equal (tetris-mit-display-test--face-at 5 5) "#0000ff"))
      (should (equal (alist-get 'score tetris-mit-display--state) 100))
      ;; an error stops polling
      (tetris-mit-display-set-provider (lambda () (error "Boom")))
      (should (tetris-mit--wait-until (lambda () (null tetris-mit-display--timer)) 5)))))

;;;; Snapshots

(ert-deftest tetris-mit-display-test-snapshot-files ()
  (tetris-mit-display-test--fresh
    (let ((frame (tetris-mit-test--rows))
          (base (make-temp-file "tetris-mit-snap")))
      (unwind-protect
          (progn
            (tetris-mit-test--set frame 0 0 [255 255 255])
            (tetris-mit-test--set frame 16 8 [0 255 255])
            (tetris-mit-test--set frame 3 3 [1 2 3])
            (tetris-mit-display-show frame 42 '((type . "state") (score . 7) (level . 0)
                                               (lines . 0) (phase . "playing")))
            (let* ((digest (tetris-mit-display-snapshot (concat base ".json")))
                   (data (tetris-mit-read-trace (concat base ".json")))
                   (text (with-temp-buffer (insert-file-contents (concat base ".txt"))
                                           (buffer-string)))
                   (ansi (with-temp-buffer (insert-file-contents (concat base ".ans"))
                                           (buffer-string))))
              (should (equal digest (tetris-mit-frame-digest frame)))
              (should (equal (alist-get 'digest data) digest))
              (should (equal (alist-get 'rows data) frame))
              (should (eql (alist-get 'frame_no data) 42))
              (should (equal (alist-get 'display_id data) "emacs-17x9"))
              (should (equal (alist-get 'state data)
                             '((score . 7) (level . 0) (lines . 0) (phase . "playing"))))
              (should (equal (car (split-string text "\n")) "W........"))
              (should (equal (nth 3 (split-string text "\n")) "...?....."))
              (should (equal (nth 16 (split-string text "\n")) "........I"))
              (should (= (cl-count ?\n ansi) 17))
              (should (string-prefix-p "\e[48;2;255;255;255m  \e[48;2;0;0;0m  " ansi))))
        (dolist (ext '("" ".json" ".ans" ".txt"))
          (when (file-exists-p (concat base ext)) (delete-file (concat base ext))))))))

(ert-deftest tetris-mit-display-test-identity ()
  (let ((identity (tetris-mit-display-identity)))
    (should (equal (alist-get 'id identity) "emacs-17x9"))
    (should (equal (alist-get 'kind identity) "emacs"))
    (should (equal (list (alist-get 'rows identity) (alist-get 'cols identity))
                   '(17 9)))))

;;;; Integration with the Python server

(defun tetris-mit-display-test--python-ansi (json-file)
  "tetris_sim.ansi.frame_to_ansi of the rows in JSON-FILE, plus a newline."
  (let ((process-environment
         (cons (concat "PYTHONPATH="
                       (expand-file-name "impl/python/engine" tetris-mit-root) path-separator
                       (expand-file-name "impl/python/sim" tetris-mit-root))
               process-environment)))
    (with-temp-buffer
      (should (= 0 (call-process
                    tetris-mit-python nil '(t nil) nil "-c"
                    "import json, sys
from tetris_sim.ansi import frame_to_ansi
rows = json.load(open(sys.argv[1]))['rows']
sys.stdout.write(frame_to_ansi(tuple(tuple(tuple(c) for c in r) for r in rows)) + '\\n')"
                    json-file)))
      (buffer-string))))

(ert-deftest tetris-mit-display-test-snapshot-digest-equals-engine ()
  "Seed + events -> snapshot; its digest is the Python engine's SPEC §9.4 digest."
  (tetris-mit-test--with-python
    (let* ((trace (tetris-mit-read-trace (tetris-mit-test--trace "09-line-clear-single")))
           (base (make-temp-file "tetris-mit-snap"))
           (result (tetris-mit-display-snapshot-run
                    (alist-get 'seed trace) (alist-get 'frames trace)
                    (alist-get 'events trace) base)))
      (unwind-protect
          (let ((engine (car (last (tetris-mit-test--engine-digests
                                    (alist-get 'seed trace) (alist-get 'frames trace)
                                    (append (alist-get 'events trace) nil))))))
            (should (equal (plist-get result :digest) engine))
            (should (equal (plist-get result :digest)
                           (aref (alist-get 'digests trace)
                                 (1- (length (alist-get 'digests trace))))))
            (should (eql (plist-get result :frame-no) (1- (alist-get 'frames trace))))
            (should (equal (with-temp-buffer
                             (insert-file-contents (concat base ".ans"))
                             (buffer-string))
                           (tetris-mit-display-test--python-ansi (concat base ".json")))))
        (dolist (ext '("" ".json" ".ans" ".txt"))
          (when (file-exists-p (concat base ext)) (delete-file (concat base ext))))
        (when (get-buffer tetris-mit-display-buffer-name)
          (kill-buffer tetris-mit-display-buffer-name))))))

(ert-deftest tetris-mit-display-test-viewer-shows-the-stream ()
  "As a viewer, the display shows what a controller plays."
  (tetris-mit-test--with-server (server port "--mode" "engine" "--clock" "lockstep")
    (let ((buffer (tetris-mit-display-connect "127.0.0.1" port "viewer"))
          (last nil))
      (unwind-protect
          (progn
            (should (tetris-mit--wait-until
                     (lambda () (buffer-local-value 'tetris-mit-display--server buffer))
                     15))
            (let ((result (tetris-mit-lockstep-run
                           "127.0.0.1" port 7 120 [[95 "hard_drop" t] [96 "hard_drop" nil]]
                           (lambda (msg) (setq last msg)))))
              (should-not (plist-get result :error)))
            (should (tetris-mit--wait-until
                     (lambda () (eql (buffer-local-value 'tetris-mit-display--frame-no buffer)
                                     119))
                     15))
            (should (equal (tetris-mit-frame-digest (tetris-mit-display-rows buffer))
                           (alist-get 'digest last)))
            (should (equal (alist-get 'phase (buffer-local-value 'tetris-mit-display--state
                                                                 buffer))
                           "playing")))
        (kill-buffer buffer)))))

(ert-deftest tetris-mit-display-test-gatekeeper-hook ()
  "The outer gatekeeper hook chooses the endpoint; the core has no reservation logic."
  (tetris-mit-test--with-server (server port "--mode" "engine" "--seed" "3")
    (let* ((seen nil)
           (tetris-mit-gatekeeper-endpoint "gatekeeper.invalid:443")
           (tetris-mit-gatekeeper-token-source (lambda () "token-123"))
           (tetris-mit-gatekeeper-function
            (lambda (request) (setq seen request) (cons "127.0.0.1" port)))
           (buffer (tetris-mit-display-connect "reserved-display.invalid" 1 "viewer")))
      (unwind-protect
          (progn
            (should (equal (plist-get seen :endpoint) "gatekeeper.invalid:443"))
            (should (equal (plist-get seen :token) "token-123"))
            (should (equal (plist-get seen :host) "reserved-display.invalid"))
            (should (equal (plist-get seen :role) "viewer"))
            (should (equal (alist-get 'id (plist-get seen :identity)) "emacs-17x9"))
            (should (tetris-mit--wait-until
                     (lambda () (buffer-local-value 'tetris-mit-display--server buffer))
                     15)))
        (kill-buffer buffer))))
  (let* ((called nil)
         (tetris-mit-gatekeeper-token-source (lambda () (setq called t) "x")))
    (should (equal (tetris-mit-resolve-endpoint "h" 1 "viewer") '("h" . 1)))
    (should-not called)))

(ert-deftest tetris-mit-display-test-snapshot-batch-is-reproducible ()
  "The docs agent's CLI: the same seed and events give byte-identical snapshots."
  (tetris-mit-test--with-python
    (let* ((dir (make-temp-file "tetris-mit-snaps" t))
           (events (expand-file-name "events.json" dir))
           (emacs (expand-file-name invocation-name invocation-directory))
           (script (list [91 "hard_drop" t] [91 "hard_drop" :json-false]
                         [100 "left" t] [100 "left" :json-false]
                         [101 "rotate_cw" t] [102 "rotate_cw" :json-false]
                         [103 "hard_drop" t] [104 "hard_drop" :json-false]))
           (process-environment (cons (concat "TETRIS_MIT_PYTHON=" tetris-mit-python)
                                      process-environment)))
      (unwind-protect
          (progn
            (with-temp-file events (insert (json-encode (vconcat script))))
            (dolist (out '("a" "b"))
              (with-temp-buffer
                (should (equal (list out 0)
                               (list out (call-process
                                          emacs nil '(t nil) nil "--batch" "-Q" "-l"
                                          (expand-file-name "contrib/emacs/tetris-mit-display.el"
                                                            tetris-mit-root)
                                          "-f" "tetris-mit-display-snapshot-batch"
                                          "7" events (expand-file-name out dir)
                                          "--frames" "150"))))
                (should (string-match-p "^snapshot .*: seed 7, frame 149, digest [0-9a-f]\\{64\\}$"
                                        (buffer-string)))))
            (dolist (ext '(".json" ".ans" ".txt"))
              (should (equal (with-temp-buffer
                               (insert-file-contents-literally
                                (expand-file-name (concat "a" ext) dir))
                               (buffer-string))
                             (with-temp-buffer
                               (insert-file-contents-literally
                                (expand-file-name (concat "b" ext) dir))
                               (buffer-string)))))
            (should (equal (alist-get 'digest (tetris-mit-read-trace
                                               (expand-file-name "a.json" dir)))
                           (car (last (tetris-mit-test--engine-digests 7 150 script))))))
        (delete-directory dir t)))))

(provide 'tetris-mit-display-test)

;;; tetris-mit-display-test.el ends here
