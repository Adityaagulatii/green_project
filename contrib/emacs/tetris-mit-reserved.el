;;; tetris-mit-reserved.el --- Play 17x9 Tetris on a reserved display  -*- lexical-binding: t; -*-

;; Copyright (C) 2026 17x9-Tetris contributors

;; Version: 0.1.1
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
;; of the relay receives inside the slot is one the display showed, in
;; order.
;;
;;   emacs --batch -Q -L contrib/emacs -l tetris-mit-reserved \
;;         -f tetris-mit-play-reserved-batch --relay URL [--key-file FILE] \
;;         [--trace TRACE.json] [--relay-display NAME] [--host H --port P]
;;
;; The display option is --relay-display, because Emacs itself takes
;; --display (the X display) from any position on the command line.
;;
;; Exit status: 0 PASS; 1 FAIL; 2 usage or setup error; 3 the relay
;; refused the key (unauthorized); 4 the key's slot ended during the
;; game.  The end of the slot takes precedence over the frame check,
;; which only covers what the relay received before the slot ended.

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

(defun tetris-mit-reserved--frames-before-cut (log)
  "Split what a relay viewer received, LOG, at the end of the lease.
LOG is a list, oldest first, of (text MESSAGE-ALIST) and (binary BYTES).
The cut is the first `lease' with holder null after one with a holder:
at the key's exp, or on a release, the relay announces it, and then (at
exp) sends a black frame that the source never showed.  Return
\(BEFORE . AFTER), the frames on each side of the cut, oldest first."
  (let (held cut before after)
    (dolist (item log)
      (pcase item
        (`(text ,msg)
         (when (equal (alist-get 'op msg) "lease")
           (if (alist-get 'holder msg)
               (setq held t)
             (when held (setq cut t)))))
        (`(binary ,bytes)
         (if cut (push bytes after) (push bytes before)))))
    (cons (nreverse before) (nreverse after))))

(defun tetris-mit-reserved--verdict (state digests-ok failure relay-match)
  "The exit status of a run whose source ended in STATE.
DIGESTS-OK says the KAV's digests matched over the frames played,
FAILURE is a setup failure or nil, and RELAY-MATCH says the relay's
frames before the cut were displayed frames, in order.  A refused key
is 3.  The end of the key's slot takes precedence over everything else:
4.  Otherwise 0 needs matching digests and frames, and a released lease."
  (cond ((eq state 'unauthorized) 3)
        ((eq state 'ended) 4)
        ((and (not failure) digests-ok relay-match (eq state 'released)) 0)
        (t 1)))

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
one frame every 1/30 s, until the trace ends or the source's lease does
\(the key's slot ending, say).  The overlay display views the server, and
`tetris-mit-display-source-game' forwards it to the relay.  A viewer of
the relay, through the bridge, records what the display there receives.

The plist has :id :frames :played :digests (matched . total, over the
frames played) :granted (the display's w h fps format) :sent :dropped
:refused :relay-frames (before the cut) :after-cut :relay-match :state
:detail :failure :exit and :verdict."
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
         (played 0) (shown nil) (watched nil)
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
                             (push (list 'binary data) watched)
                           (let ((msg (ignore-errors (tetris-mit--json-parse data))))
                             (when (consp msg) (push (list 'text msg) watched)))))))
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
                  shown)
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
                ;; The scripted user: the trace's inputs, one frame per
                ;; 1/30 s, for as long as the display is ours.
                (let ((next (float-time)))
                  (while (and (< played frames)
                              (eq (tetris-mit-display-source-state source) 'granted))
                    (apply #'tetris-mit-send tetris-mit--process
                           (append (mapcar (lambda (e) (tetris-mit-make-event (car e) (cdr e)))
                                           (aref steps played))
                                   (list (tetris-mit-make-tick 1))))
                    (setq played (1+ played)
                          next (+ next (/ 1.0 tetris-mit-fps)))
                    (tetris-mit-reserved--pace next)))
                (when (> played 0)
                  (tetris-mit--wait-until (lambda () (aref digests (1- played))) 10))
                (tetris-mit-reserved--pace (+ (float-time) 0.3))))))
          (when (eq (tetris-mit-display-source-state source) 'granted)
            (tetris-mit-display-source-stop source))
          ;; Let the viewer see the end of the lease.
          (tetris-mit--wait-until
           (lambda ()
             (cl-some (lambda (item)
                        (and (eq (car item) 'text)
                             (equal (alist-get 'op (cadr item)) "lease")
                             (null (alist-get 'holder (cadr item)))))
                      watched))
           3))
      (remove-hook 'tetris-mit-display-frame-functions collect)
      (when (and source (memq source tetris-mit-display-source--sources))
        (tetris-mit-display-source-stop source))
      (when (buffer-live-p game) (kill-buffer game))
      (when (buffer-live-p screen) (kill-buffer screen))
      (when (and watch (process-live-p watch)) (delete-process watch))
      (when server (tetris-mit-stop-server server)))
    (let* ((actual (cl-loop for k below played
                            when (or (zerop (% k every)) (= k (1- frames)))
                            collect (aref digests k)))
           (matched (cl-loop for a in actual for e across expected count (equal a e)))
           (split (tetris-mit-reserved--frames-before-cut (reverse watched)))
           (relay-match (and (car split)
                             (tetris-mit-reserved--subsequence-p (car split)
                                                                 (reverse shown))
                             t))
           (state (tetris-mit-display-source-state source))
           (digests-ok (and (> played 0) (= matched (length actual))))
           (exit (tetris-mit-reserved--verdict state digests-ok failure relay-match)))
      (list :id id :frames frames :played played :digests (cons matched (length actual))
            :granted (list (tetris-mit-display-source-w source)
                           (tetris-mit-display-source-h source)
                           (tetris-mit-display-source-fps source)
                           (tetris-mit-display-source-format source))
            :sent (tetris-mit-display-source-sent source)
            :dropped (tetris-mit-display-source-dropped source)
            :refused (tetris-mit-display-source-rejected source)
            :relay-frames (length (car split)) :after-cut (length (cdr split))
            :relay-match relay-match
            :state state :detail (tetris-mit-display-source-detail source)
            :failure failure :exit exit
            :verdict (pcase exit (0 "PASS") (3 "UNAUTHORIZED") (4 "SLOT ENDED")
                            (_ "FAIL"))))))

(defun tetris-mit-play-reserved--parse-args (args)
  "Parse the batch entry point's command-line ARGS into a plist.
The plist has :relay :key :key-file :trace :display :host :port.
Signal an error, whose message starts with \"usage\" or \"unknown\", for
a missing --relay or an unknown argument.  The display option is
--relay-display: Emacs itself takes --display, from anywhere on the
command line, so a --display never gets here."
  (let (plist)
    (while args
      (let ((arg (pop args)))
        (pcase arg
          ("--relay" (setq plist (plist-put plist :relay (pop args))))
          ("--key" (setq plist (plist-put plist :key (pop args))))
          ("--key-file" (setq plist (plist-put plist :key-file (pop args))))
          ("--trace" (setq plist (plist-put plist :trace (expand-file-name (pop args)))))
          ("--relay-display" (setq plist (plist-put plist :display (pop args))))
          ("--host" (setq plist (plist-put plist :host (pop args))))
          ("--port" (setq plist (plist-put plist :port (string-to-number (or (pop args) "")))))
          (_ (error "unknown argument %s" arg)))))
    (unless (plist-get plist :relay)
      (error "usage: emacs --batch -Q -L contrib/emacs -l tetris-mit-reserved \
-f tetris-mit-play-reserved-batch --relay URL [--key KEY | --key-file FILE] \
[--trace TRACE.json] [--relay-display NAME] [--host H --port P]"))
    plist))

(defun tetris-mit-play-reserved-batch ()
  "Batch entry point: `tetris-mit-play-reserved-run' from the command line.
  emacs --batch -Q -L contrib/emacs -l tetris-mit-reserved \\
        -f tetris-mit-play-reserved-batch --relay URL \\
        [--key KEY | --key-file FILE] [--trace TRACE.json] \\
        [--relay-display NAME] [--host H --port P]
The trace defaults to KAV-07 (spec/conformance/traces/07-hard-drop.json).
Print a report, then a line \"RESULT {json}\", and exit with 0 PASS, 1
FAIL, 2 usage or setup error, 3 unauthorized, or 4 slot ended."
  (let ((args (condition-case err
                  (tetris-mit-play-reserved--parse-args
                   (prog1 command-line-args-left (setq command-line-args-left nil)))
                (error (princ (format "tetris-mit-play-reserved-batch: %s\n"
                                      (error-message-string err)))
                       (kill-emacs 2)))))
    (let ((trace (or (plist-get args :trace)
                     (expand-file-name "07-hard-drop.json" (tetris-mit-trace-directory))))
          (display (plist-get args :display))
          (relay (plist-get args :relay))
          (tetris-mit-display-source-key-file (or (plist-get args :key-file)
                                                  tetris-mit-display-source-key-file)))
      (condition-case err
          (let* ((key (tetris-mit-display-source-read-key (plist-get args :key)))
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
                            :host (plist-get args :host) :port (plist-get args :port)))))
            (princ (format "play-reserved: display granted %S (w h fps format)\n"
                           (plist-get result :granted)))
            (princ (format "play-reserved: %s: %d/%d digests match on the Emacs display, \
over %d of %d frames played\n"
                           (plist-get result :id) (car (plist-get result :digests))
                           (cdr (plist-get result :digests))
                           (plist-get result :played) (plist-get result :frames)))
            (princ (format "play-reserved: source sent %d frames (%d dropped, %d refused); \
relay viewer saw %d in the slot, %s; %d after its end\n"
                           (plist-get result :sent) (plist-get result :dropped)
                           (plist-get result :refused) (plist-get result :relay-frames)
                           (if (plist-get result :relay-match)
                               "each one a displayed frame, in order"
                             "NOT matching the displayed frames")
                           (plist-get result :after-cut)))
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
                              (frames . ,(plist-get result :frames))
                              (played . ,(plist-get result :played))
                              (digests_matched . ,(car (plist-get result :digests)))
                              (digests_total . ,(cdr (plist-get result :digests)))
                              (sent . ,(plist-get result :sent))
                              (dropped . ,(plist-get result :dropped))
                              (refused . ,(plist-get result :refused))
                              (relay_frames . ,(plist-get result :relay-frames))
                              (relay_frames_after_end . ,(plist-get result :after-cut))
                              (relay_match . ,(if (plist-get result :relay-match) t :json-false))
                              (state . ,(symbol-name (plist-get result :state)))
                              (detail . ,(plist-get result :detail))))))
            (kill-emacs (plist-get result :exit)))
        (error (princ (format "tetris-mit-play-reserved-batch: %s\n"
                              (error-message-string err)))
               (kill-emacs 2))))))

(provide 'tetris-mit-reserved)

;;; tetris-mit-reserved.el ends here
