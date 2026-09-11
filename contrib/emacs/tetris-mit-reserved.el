;;; tetris-mit-reserved.el --- Play 17x9 Tetris on a reserved display  -*- lexical-binding: t; -*-

;; Copyright (C) 2026 17x9-Tetris contributors

;; Version: 0.1.0
;; Package-Requires: ((emacs "28.1"))
;; Keywords: games
;; URL: https://github.com/aygp-dr/17x9-Tetris

;; This file is not part of GNU Emacs.

;;; Commentary:

;; The user's scenario for experiment 002 (signed display lease keys):
;; "the User interacting with interactive programs like tetris then the
;; event loop interacting with Display".  Three connections make it up:
;;
;; - the user plays in `tetris-mit-remote', the controller of the engine
;;   server (docs/PROTOCOL.md, contract v1);
;; - the overlay display (`tetris-mit-display') is a viewer of the same
;;   server;
;; - `tetris-mit-display-source-game' forwards what the display shows to
;;   a display relay (wal.sh/tools/display v0.2.1), with the dlk1 lease key
;;   that the reservation system issued.  It honours the display's
;;   capabilities (grid, palette, fps, format) and the key's slot.
;;
;; `tetris-mit-play-reserved' sets this up interactively.
;; `tetris-mit-play-reserved-batch' is the unattended variant for the
;; reservation harness: a scripted user whose key presses and releases
;; are one KAV's, on a lockstep server, at 30 frames a second.  It checks
;; the KAV's digests on the Emacs display, and that every frame a viewer
;; of the relay receives is one the display showed, in order.
;;
;;   emacs --batch -Q -L contrib/emacs -l tetris-mit-reserved \
;;         -f tetris-mit-play-reserved-batch --relay URL [--key-file FILE] \
;;         [--trace TRACE.json] [--display NAME] [--host H --port P]
;;
;; Exit status: 0 PASS; 1 FAIL; 2 usage or setup error; 3 the relay
;; refused the key (unauthorized); 4 the key's slot ended during the game.

;;; Code:

(require 'cl-lib)
(require 'json)
(require 'tetris-mit)
(require 'tetris-mit-display)
(require 'tetris-mit-display-source)

;;;###autoload
(defun tetris-mit-play-reserved (&optional host port display url key)
  "Play on the engine server at HOST:PORT, shown on the reserved DISPLAY.
The overlay display views the server, `tetris-mit-display-source-game'
forwards its frames to the relay at URL with the dlk1 lease KEY, and
`tetris-mit-remote' is where you play.  KEY defaults to
`tetris-mit-display-source-key', then to the key file.  The relay's
answers show in the echo area: a refused key stops the source, and so
does the end of the key's slot."
  (interactive
   (list (read-string "Engine host: " tetris-mit-host)
         (read-number "Engine port: " tetris-mit-port)
         (tetris-mit-display-source--read-display)
         (read-string "Display relay: " tetris-mit-display-source-url)
         (let ((key (read-string "dlk1 key (empty: the key option or file): ")))
           (and (not (string-empty-p key)) key))))
  (let* ((host (or host tetris-mit-host))
         (port (or port tetris-mit-port))
         (screen (tetris-mit-display-connect host port "viewer")))
    (tetris-mit-display-source-game (or display tetris-mit-display-source-display)
                                    url nil key)
    (let ((game (tetris-mit-remote host port)))
      (save-selected-window
        (display-buffer screen '(display-buffer-pop-up-window)))
      game)))

;;;; The unattended variant

(defun tetris-mit-reserved--subsequence-p (items sequence)
  "Non-nil if ITEMS appear in SEQUENCE in order (compared with `equal')."
  (let ((rest sequence))
    (cl-every (lambda (item)
                (let ((tail (cl-member item rest :test #'equal)))
                  (when tail (setq rest (cdr tail)) t)))
              items)))

(defun tetris-mit-reserved--pace (until)
  "Process input until the time UNTIL (`float-time')."
  (let (left)
    (while (> (setq left (- until (float-time))) 0)
      (accept-process-output nil (min left 0.05)))))

(cl-defun tetris-mit-play-reserved-run (&key relay key trace display host port)
  "Play the KAV TRACE on a reserved display, as a scripted user; return a plist.
RELAY is the relay's URL, KEY a dlk1 key (or nil, for a relay without
lease secrets), DISPLAY the display (default
`tetris-mit-display-source-display').  Without PORT, a private lockstep
engine server is started; otherwise HOST:PORT must be a lockstep server.
The scripted user is the controller (`tetris-mit-remote', with the
trace's seed): it sends the trace's key presses and releases, and ticks
one frame every 1/30 s.  The overlay display views the server, and
`tetris-mit-display-source-game' forwards it to the relay.  A viewer of
the relay, through the bridge, records what the display there receives.

The plist has :id :frames :digests (matched . total) :granted (the
display's w h fps format) :sent :dropped :refused :relay-frames
:relay-match :state :detail :exit and :verdict."
  (let* ((trace-data (tetris-mit-read-trace trace))
         (id (tetris-mit-kav-id trace))
         (seed (alist-get 'seed trace-data))
         (frames (alist-get 'frames trace-data))
         (every (alist-get 'digest_every trace-data))
         (expected (alist-get 'digests trace-data))
         (steps (tetris-mit--trace-step-events (alist-get 'events trace-data) frames))
         (display (or display tetris-mit-display-source-display))
         (server (unless port
                   (tetris-mit-start-server "--mode" "engine" "--clock" "lockstep")))
         (host (or host "127.0.0.1"))
         (port (or port (cdr server)))
         (digests (make-vector frames nil))
         (shown nil) (relay-frames nil) (leases nil)
         (source nil) (screen nil) (game nil) (watch nil) (failure nil)
         (collect (lambda (rows frame-no)
                    (when (and frame-no (< frame-no frames))
                      (aset digests frame-no (tetris-mit-frame-digest rows)))
                    (when (and source (eq (tetris-mit-display-source-state source) 'granted))
                      (push (tetris-mit-display-source-indices
                             rows (tetris-mit-display-source-w source)
                             (tetris-mit-display-source-h source)
                             (tetris-mit-display-source-palette source))
                            shown)))))
    (unwind-protect
        (progn
          (setq watch (tetris-mit-display-source-watch
                       relay display
                       (lambda (kind data)
                         (if (eq kind 'binary)
                             (push data relay-frames)
                           (let ((msg (ignore-errors (tetris-mit--json-parse data))))
                             (when (equal (alist-get 'op msg) "lease")
                               (push msg leases)))))))
          (setq screen (tetris-mit-display-connect host port "viewer"))
          (unless (tetris-mit--wait-until
                   (lambda () (buffer-local-value 'tetris-mit-display--server screen)) 15)
            (error "The display got no hello from %s:%s" host port))
          (add-hook 'tetris-mit-display-frame-functions collect)
          (setq source (tetris-mit-display-source-game display relay nil key))
          (tetris-mit--wait-until
           (lambda () (not (eq (tetris-mit-display-source-state source) 'reserving))) 15)
          (when (eq (tetris-mit-display-source-state source) 'granted)
            ;; On the grant, the source sent what the display showed then.
            (push (tetris-mit-display-source-indices
                   (tetris-mit-display-rows screen) (tetris-mit-display-source-w source)
                   (tetris-mit-display-source-h source)
                   (tetris-mit-display-source-palette source))
                  shown))
          (when (eq (tetris-mit-display-source-state source) 'granted)
            (setq game (save-window-excursion (tetris-mit-remote host port seed)))
            (with-current-buffer game
              (unless (tetris-mit--wait-until (lambda () tetris-mit--server) 15)
                (error "The controller got no hello from %s:%s" host port))
              (cond
               ((not (equal (alist-get 'clock tetris-mit--server) "lockstep"))
                (setq failure "the engine server is not in lockstep"))
               ((not (equal (tetris-mit-client-role tetris-mit--process) "controller"))
                (setq failure (format "the server admitted the user as a %s"
                                      (tetris-mit-client-role tetris-mit--process))))
               (t
                ;; The scripted user: the trace's inputs, one frame per 1/30 s.
                (let ((next (float-time)))
                  (dotimes (k frames)
                    (apply #'tetris-mit-send tetris-mit--process
                           (append (mapcar (lambda (e) (tetris-mit-make-event (car e) (cdr e)))
                                           (aref steps k))
                                   (list (tetris-mit-make-tick 1))))
                    (setq next (+ next (/ 1.0 tetris-mit-fps)))
                    (tetris-mit-reserved--pace next)))
                (tetris-mit--wait-until (lambda () (aref digests (1- frames))) 10)
                (tetris-mit-reserved--pace (+ (float-time) 0.3))))))
          (when (eq (tetris-mit-display-source-state source) 'granted)
            (tetris-mit-display-source-stop source)
            (tetris-mit--wait-until
             (lambda () (and leases (null (alist-get 'holder (car leases))))) 3)))
      (remove-hook 'tetris-mit-display-frame-functions collect)
      (when (and source (memq source tetris-mit-display-source--sources))
        (tetris-mit-display-source-stop source))
      (when (buffer-live-p game) (kill-buffer game))
      (when (buffer-live-p screen) (kill-buffer screen))
      (when (and watch (process-live-p watch)) (delete-process watch))
      (when server (tetris-mit-stop-server server)))
    (let* ((actual (cl-loop for k below frames
                            when (or (zerop (% k every)) (= k (1- frames)))
                            collect (aref digests k)))
           (matched (cl-loop for a in actual for e across expected count (equal a e)))
           (relay-frames (nreverse relay-frames))
           (relay-match (and relay-frames
                             (tetris-mit-reserved--subsequence-p relay-frames
                                                                 (nreverse shown))))
           (state (tetris-mit-display-source-state source))
           (exit (cond ((eq state 'unauthorized) 3)
                       ((eq state 'ended) (if relay-match 4 1))
                       ((and (not failure) (= matched (length expected)) relay-match
                             (eq state 'released))
                        0)
                       (t 1))))
      (list :id id :frames frames :digests (cons matched (length expected))
            :granted (list (tetris-mit-display-source-w source)
                           (tetris-mit-display-source-h source)
                           (tetris-mit-display-source-fps source)
                           (tetris-mit-display-source-format source))
            :sent (tetris-mit-display-source-sent source)
            :dropped (tetris-mit-display-source-dropped source)
            :refused (tetris-mit-display-source-rejected source)
            :relay-frames (length relay-frames) :relay-match (and relay-match t)
            :state state :detail (tetris-mit-display-source-detail source)
            :failure failure :exit exit
            :verdict (pcase exit (0 "PASS") (3 "UNAUTHORIZED") (4 "SLOT ENDED")
                            (_ "FAIL"))))))

(defun tetris-mit-play-reserved-batch ()
  "Batch entry point: `tetris-mit-play-reserved-run' from the command line.
  emacs --batch -Q -L contrib/emacs -l tetris-mit-reserved \\
        -f tetris-mit-play-reserved-batch --relay URL \\
        [--key KEY | --key-file FILE] [--trace TRACE.json] [--display NAME] \\
        [--host H --port P]
The trace defaults to KAV-07 (spec/conformance/traces/07-hard-drop.json).
Print a report, then a line \"RESULT {json}\", and exit with 0 PASS, 1
FAIL, 2 usage or setup error, 3 unauthorized, or 4 slot ended."
  (let (relay key key-file trace display host port)
    (while command-line-args-left
      (let ((arg (pop command-line-args-left)))
        (pcase arg
          ("--relay" (setq relay (pop command-line-args-left)))
          ("--key" (setq key (pop command-line-args-left)))
          ("--key-file" (setq key-file (pop command-line-args-left)))
          ("--trace" (setq trace (expand-file-name (pop command-line-args-left))))
          ("--display" (setq display (pop command-line-args-left)))
          ("--host" (setq host (pop command-line-args-left)))
          ("--port" (setq port (string-to-number (or (pop command-line-args-left) ""))))
          (_ (princ (format "tetris-mit-play-reserved-batch: unknown argument %s\n" arg))
             (kill-emacs 2)))))
    (unless relay
      (princ "usage: emacs --batch -Q -L contrib/emacs -l tetris-mit-reserved \
-f tetris-mit-play-reserved-batch --relay URL [--key KEY | --key-file FILE] \
[--trace TRACE.json] [--display NAME] [--host H --port P]\n")
      (kill-emacs 2))
    (let ((trace (or trace (expand-file-name "07-hard-drop.json"
                                             (tetris-mit-trace-directory))))
          (tetris-mit-display-source-key-file (or key-file
                                                  tetris-mit-display-source-key-file)))
      (condition-case err
          (let* ((key (tetris-mit-display-source-read-key key))
                 (claims (and key (tetris-mit-display-source-key-claims key)))
                 (result (progn
                           (princ (format "play-reserved: %s (%s) on display %s at %s%s\n"
                                          (tetris-mit-kav-id trace)
                                          (file-name-nondirectory trace)
                                          (or display tetris-mit-display-source-display)
                                          relay
                                          (if claims
                                              (format ", key sub=%s rid=%s exp=%s"
                                                      (alist-get 'sub claims)
                                                      (alist-get 'rid claims)
                                                      (alist-get 'exp claims))
                                            (if key ", key (not dlk1)" ", no key"))))
                           (tetris-mit-play-reserved-run
                            :relay relay :key key :trace trace :display display
                            :host host :port port))))
            (princ (format "play-reserved: display granted %S (w h fps format)\n"
                           (plist-get result :granted)))
            (princ (format "play-reserved: %s: %d/%d digests match on the Emacs display\n"
                           (plist-get result :id) (car (plist-get result :digests))
                           (cdr (plist-get result :digests))))
            (princ (format "play-reserved: source sent %d frames (%d dropped, %d refused); \
relay viewer saw %d, %s\n"
                           (plist-get result :sent) (plist-get result :dropped)
                           (plist-get result :refused) (plist-get result :relay-frames)
                           (if (plist-get result :relay-match)
                               "each one a displayed frame, in order"
                             "NOT matching the displayed frames")))
            (princ (format "%s%s\n" (plist-get result :verdict)
                           (cond ((plist-get result :detail)
                                  (format ": %s" (plist-get result :detail)))
                                 ((plist-get result :failure)
                                  (format ": %s" (plist-get result :failure)))
                                 (t ""))))
            (princ (format "RESULT %s\n"
                           (json-encode
                            `((id . ,(plist-get result :id))
                              (verdict . ,(plist-get result :verdict))
                              (exit . ,(plist-get result :exit))
                              (digests_matched . ,(car (plist-get result :digests)))
                              (digests_total . ,(cdr (plist-get result :digests)))
                              (sent . ,(plist-get result :sent))
                              (dropped . ,(plist-get result :dropped))
                              (refused . ,(plist-get result :refused))
                              (relay_frames . ,(plist-get result :relay-frames))
                              (relay_match . ,(if (plist-get result :relay-match) t :json-false))
                              (state . ,(symbol-name (plist-get result :state)))
                              (detail . ,(plist-get result :detail))))))
            (kill-emacs (plist-get result :exit)))
        (error (princ (format "tetris-mit-play-reserved-batch: %s\n"
                              (error-message-string err)))
               (kill-emacs 2))))))

(provide 'tetris-mit-reserved)

;;; tetris-mit-reserved.el ends here
