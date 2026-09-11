;;; record-game.el --- Play a recorded game on the stock Emacs display  -*- lexical-binding: t; -*-

;; Copyright (C) 2026 17x9-Tetris contributors

;; This file is not part of GNU Emacs.

;;; Commentary:

;; What contrib/emacs/media/record.sh runs inside `emacs -nw', under
;; asciinema.  It changes no code of the display, and only sets options
;; and a header line:
;;
;;   emacs -nw -Q -L contrib/emacs -l contrib/emacs/media/record-game.el
;;
;; 1. It starts a private lockstep engine server (`tetris-mit-start-server',
;;    the SPEC engine in impl/python) and connects as the controller, with
;;    the seed of the trace green-building-game.json.
;; 2. It sends the trace's inputs as protocol events and ticks, paced like
;;    a controller that owns time: at most 5 frames per tick, and never
;;    more than `tetris-mit-record-lead' frames ahead of the frame on
;;    screen.  (Unpaced, the server runs several times faster than real
;;    time, and decoding its stream starves Emacs's redisplay.)
;; 3. It shows each frame on the stock overlay display (`tetris-mit-display',
;;    9 cells wide and 17 tall) at 30 FPS, with an in-process provider
;;    (`tetris-mit-display-set-provider').  A tick of the provider that
;;    finds no frame is counted as a stall.
;; 4. It checks every frame's SPEC §9.4 digest against the trace, which
;;    pins all of them, writes a summary to $TETRIS_MIT_RECORD_SUMMARY, if
;;    set, and exits.
;;
;; The one option that shapes the picture is the existing
;; `tetris-mit-cell-string': three spaces here ($TETRIS_MIT_CELL), so a
;; window is 3 columns by 1 line.  With a terminal font whose cell is
;; about 1:2, that is the Green Building preset's cell aspect of 1.5.

;;; Code:

(require 'tetris-mit-display)

(defvar tetris-mit-record-trace
  (expand-file-name "green-building-game.json"
                    (file-name-directory (or load-file-name buffer-file-name)))
  "The trace to play.")

(defvar tetris-mit-record-lead 90
  "Most frames ticked ahead of the frame on screen (3 s at 30 FPS).")

(defvar tetris-mit-record--trace nil "The trace being played.")
(defvar tetris-mit-record--server nil "The private server, (PROCESS . PORT).")
(defvar tetris-mit-record--proc nil "The controller connection.")
(defvar tetris-mit-record--queue nil "Message groups not sent yet, (MSGS . FRAMES).")
(defvar tetris-mit-record--sent 0 "Frames ticked so far.")
(defvar tetris-mit-record--frames nil "Vector of (ROWS DIGEST STATE), by frame.")
(defvar tetris-mit-record--next 0 "Index of the next frame to show.")
(defvar tetris-mit-record--stalls 0 "Ticks of the provider that found no frame.")
(defvar tetris-mit-record--failure nil "Why the replay failed, or nil.")
(defvar tetris-mit-record--started nil "When the playback started (`float-time').")

(defun tetris-mit-record--groups (events frames)
  "EVENTS, [FRAME ACTION DOWN] sorted by frame, over FRAMES, as message groups.
Return a list of (MSGS . N): MSGS ends with a tick of N frames, at most
5, and starts with the events of the tick's first frame.  The events of
frame k are thus sent just before the tick that runs frame k."
  (let ((i 0) (n (length events)) (k 0) groups)
    (while (< k frames)
      (let (msgs)
        (while (and (< i n) (<= (aref (aref events i) 0) k))
          (let ((e (aref events i)))
            (push (tetris-mit-make-event (aref e 1) (eq (aref e 2) t)) msgs))
          (setq i (1+ i)))
        (let* ((next (if (< i n) (min (aref (aref events i) 0) frames) frames))
               (ticks (min (max 1 (- next k)) 5)))
          (push (tetris-mit-make-tick ticks) msgs)
          (push (cons (nreverse msgs) ticks) groups)
          (setq k (+ k ticks)))))
    (nreverse groups)))

(defun tetris-mit-record--pump ()
  "Send message groups until the lead is `tetris-mit-record-lead' frames."
  (while (and tetris-mit-record--queue (process-live-p tetris-mit-record--proc)
              (< (- tetris-mit-record--sent tetris-mit-record--next)
                 tetris-mit-record-lead))
    (let ((group (pop tetris-mit-record--queue)))
      (apply #'tetris-mit-send tetris-mit-record--proc (car group))
      (setq tetris-mit-record--sent (+ tetris-mit-record--sent (cdr group))))))

(defun tetris-mit-record--receive (_proc msg)
  "Handle MSG from the engine server."
  (let ((trace tetris-mit-record--trace))
    (pcase (alist-get 'type msg)
      ("hello"
       (if (and (equal (alist-get 'clock msg) "lockstep")
                (eql (alist-get 'seed msg) (alist-get 'seed trace)))
           (tetris-mit-record--pump)
         (setq tetris-mit-record--failure
               (format "the server is not a lockstep server with seed %s"
                       (alist-get 'seed trace)))))
      ("frame"
       (let ((rows (alist-get 'rows msg)))
         (aset tetris-mit-record--frames (alist-get 'frame_no msg)
               (list rows (tetris-mit-frame-digest rows) nil))))
      ;; A state follows its frame (PROTOCOL §4.3) and names it.
      ("state"
       (let ((entry (aref tetris-mit-record--frames (alist-get 'frame_no msg))))
         (when entry (setf (nth 2 entry) msg))))
      ("error"
       (setq tetris-mit-record--failure
             (format "%s: %s" (alist-get 'code msg) (alist-get 'message msg)))))))

(defun tetris-mit-record--provider ()
  "The next frame as a frame alist, or nil if it has not arrived.
A frame is shown once the next one has arrived too, so that the state
message that follows it is in."
  (let* ((frames tetris-mit-record--frames)
         (total (length frames))
         (k tetris-mit-record--next))
    (tetris-mit-record--pump)
    (cond
     ((or tetris-mit-record--failure (>= k total))
      (tetris-mit-display-set-provider nil)
      (tetris-mit-record--done)
      nil)
     ((and (aref frames k) (or (= (1+ k) total) (aref frames (1+ k))))
      (setq tetris-mit-record--next (1+ k))
      (let ((entry (aref frames k)))
        `((rows . ,(nth 0 entry)) (frame_no . ,k)
          ,@(and (nth 2 entry) `((state . ,(nth 2 entry)))))))
     (t (when (> k 0) (setq tetris-mit-record--stalls (1+ tetris-mit-record--stalls)))
        nil))))

(defun tetris-mit-record--done ()
  "Check the digests, show the summary, and exit 3 s later."
  (let* ((trace tetris-mit-record--trace)
         (expected (alist-get 'digests trace))
         (frames tetris-mit-record--frames)
         (total (length frames))
         (matched (cl-loop for k below total
                           count (equal (nth 1 (aref frames k)) (aref expected k))))
         (pass (and (not tetris-mit-record--failure) (= matched total)))
         (summary (format "%s: %d/%d digests match the trace, %d stalls -- %s"
                          (alist-get 'name trace) matched total
                          tetris-mit-record--stalls (if pass "PASS" "FAIL"))))
    (when tetris-mit-record--failure
      (setq summary (concat summary " (" tetris-mit-record--failure ")")))
    ;; The wall-clock time of the playback: a loaded host runs Emacs's
    ;; 30 FPS timer late, which no stall counts.
    (setq summary (format "%s\nplayed %d frames in %.1f s (%.1f s at %d FPS)"
                          summary (length frames)
                          (- (float-time) tetris-mit-record--started)
                          (/ (length frames) (float tetris-mit-fps)) tetris-mit-fps))
    (when (process-live-p tetris-mit-record--proc)
      (delete-process tetris-mit-record--proc))
    (tetris-mit-stop-server tetris-mit-record--server)
    (message "%s" (car (split-string summary "\n")))
    (run-at-time 3 nil #'tetris-mit-record--finish summary pass)))

(defun tetris-mit-record--finish (summary pass)
  "Write SUMMARY to $TETRIS_MIT_RECORD_SUMMARY and exit; 0 if PASS."
  (let ((file (getenv "TETRIS_MIT_RECORD_SUMMARY")))
    (when file
      (with-temp-file file (insert summary "\n"))))
  (kill-emacs (if pass 0 1)))

(defun tetris-mit-record-setup ()
  "Show the empty display, its header line and the game's description.
This runs when the file is loaded, before Emacs first draws the screen."
  (let ((trace (tetris-mit-read-trace tetris-mit-record-trace))
        (buffer (tetris-mit-display-buffer)))
    (setq tetris-mit-record--trace trace
          tetris-mit-record--queue (tetris-mit-record--groups
                                    (alist-get 'events trace) (alist-get 'frames trace))
          tetris-mit-record--sent 0
          tetris-mit-record--frames (make-vector (alist-get 'frames trace) nil)
          tetris-mit-record--next 0
          tetris-mit-record--stalls 0
          tetris-mit-record--failure nil)
    (switch-to-buffer buffer)
    (delete-other-windows)
    (with-current-buffer buffer
      (setq header-line-format
            (format " MIT Green Building: %d x %d windows, each %d columns x 1 line"
                    tetris-mit-cols tetris-mit-rows (length tetris-mit-cell-string)))
      ;; Park the terminal cursor on the blank line under the grid, so it
      ;; does not sit on window (0, 0).
      (goto-char (point-min))
      (forward-line tetris-mit-rows)
      (set-window-point (selected-window) (point)))
    (message "Seed %d: %d recorded inputs into the SPEC engine server, %d frames at %d FPS"
             (alist-get 'seed trace) (length (alist-get 'events trace))
             (alist-get 'frames trace) tetris-mit-fps)))

(defun tetris-mit-record-start ()
  "Start the server, drive it with the trace, and play its frames."
  (let ((trace tetris-mit-record--trace)
        (buffer (tetris-mit-display-buffer)))
    (setq tetris-mit-record--server
          (tetris-mit-start-server "--mode" "engine" "--clock" "lockstep"))
    (setq tetris-mit-record--proc
          (tetris-mit-open "tetris-mit-record" "127.0.0.1" (cdr tetris-mit-record--server)
                           "controller" #'tetris-mit-record--receive
                           `((seed . ,(alist-get 'seed trace)))))
    ;; An invisible marker (it sets the terminal title): record.sh folds
    ;; the startup before it into the cast's first frame.
    (unless noninteractive
      (send-string-to-terminal "\e]2;tetris-mit-record: play\a"))
    (setq tetris-mit-record--started (float-time))
    (tetris-mit-display-set-provider #'tetris-mit-record--provider buffer)))

;; record.sh also sets the first two in its init file, before the
;; terminal is set up; they are repeated for "emacs -nw -Q -l".
(setq inhibit-startup-screen t
      ;; Fewer, later garbage collections: a long one is a visible stall.
      gc-cons-threshold (* 64 1024 1024))
(menu-bar-mode -1)
(setq tetris-mit-cell-string (or (getenv "TETRIS_MIT_CELL") "   "))
(tetris-mit-record-setup)
(run-at-time 0.3 nil #'tetris-mit-record-start)

;;; record-game.el ends here
