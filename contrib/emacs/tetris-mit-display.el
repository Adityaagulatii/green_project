;;; tetris-mit-display.el --- Emacs as a 17x9 facade display  -*- lexical-binding: t; -*-

;; Copyright (C) 2026 17x9-Tetris contributors

;; Version: 0.1.0
;; Package-Requires: ((emacs "28.1"))
;; Keywords: games
;; URL: https://github.com/aygp-dr/17x9-Tetris

;; This file is not part of GNU Emacs.

;;; Commentary:

;; Emacs as a SPEC §2.3 Display.  The 153 windows of the Green Building
;; facade are 17 rows by 9 columns, so the display is 9 cells wide and
;; 17 tall.  Row 0 is at the top (SPEC §0, §2.1).
;;
;; `tetris-mit-display' builds the grid once, with one overlay per
;; cell.  A frame then only repaints the overlays whose color changed,
;; using face specs cached per color, so a frame allocates nothing.
;; The faces are plain RGB backgrounds: exact on a graphical or
;; truecolor display, and mapped by Emacs to the nearest tty color in
;; "emacs -nw".
;;
;; Frames can come from any of these sources:
;; - a protocol server, as viewer or controller
;;   (`tetris-mit-display-connect', docs/PROTOCOL.md);
;; - an in-process frame provider that is called at 30 FPS
;;   (`tetris-mit-display-set-provider');
;; - direct calls of `tetris-mit-display-show'.
;;
;; A frame is the `rows' payload of a protocol frame message: a vector
;; of 17 rows, top first, each a vector of 9 [R G B] vectors of integers
;; in 0..255.
;;
;; `tetris-mit-display-snapshot' writes the display as text (.ans with
;; ANSI truecolor, and .txt with palette codes) and as .json (the RGB
;; cells, frame_no and the SPEC §9.4 digest).  It also works in batch:
;;   emacs --batch -l tetris-mit-display.el \
;;         -f tetris-mit-display-snapshot-batch SEED EVENTS OUT

;;; Code:

(require 'cl-lib)
(require 'json)
(require 'tetris-mit
         (let ((dir (file-name-directory
                     (or load-file-name (bound-and-true-p byte-compile-current-file)
                         buffer-file-name default-directory))))
           (locate-file "tetris-mit" (list dir) (get-load-suffixes))))

(defgroup tetris-mit-display nil
  "Emacs as a 17x9 facade display."
  :group 'tetris-mit
  :prefix "tetris-mit-display-")

(defcustom tetris-mit-display-id "emacs-17x9"
  "Identity of this display.
An outer reservation system can register the display under this
identity.  The reservation system is a separate project, and this
package contains no reservation logic."
  :type 'string)

(defcustom tetris-mit-display-kind "emacs"
  "Kind of this display, for an outer reservation system."
  :type 'string)

(defcustom tetris-mit-display-show-frame-number nil
  "Non-nil means the status line also shows the frame number.
The status line then changes on every frame."
  :type 'boolean)

(defconst tetris-mit-display-buffer-name "*tetris-mit-display*"
  "Buffer of the display.")

(defconst tetris-mit-display--cells (* tetris-mit-rows tetris-mit-cols)
  "Number of windows: 153.")

(defvar tetris-mit-display--faces (make-hash-table :test #'eql)
  "Face specs, (:background \"#rrggbb\"), keyed by packed #xRRGGBB color.")

(defvar-local tetris-mit-display--overlays nil "The 153 cell overlays, row-major.")
(defvar-local tetris-mit-display--colors nil "The 153 packed colors on screen.")
(defvar-local tetris-mit-display--frame-no nil "Number of the frame shown.")
(defvar-local tetris-mit-display--frames 0 "Frames shown since the grid was built.")
(defvar-local tetris-mit-display--changed 0 "Overlays repainted by the last frame.")
(defvar-local tetris-mit-display--state nil "The last state alist.")
(defvar-local tetris-mit-display--note nil "Source note for the status line.")
(defvar-local tetris-mit-display--status nil "Marker at the status line.")
(defvar-local tetris-mit-display--process nil "Connection to a server.")
(defvar-local tetris-mit-display--role nil "Role of that connection.")
(defvar-local tetris-mit-display--server nil "The server's hello.")
(defvar-local tetris-mit-display--last-error nil "The last error message.")
(defvar-local tetris-mit-display--timer nil "Timer that polls the frame provider.")

;;;; Identity

(defun tetris-mit-display-identity ()
  "This display's identity: an alist an outer reservation system can register."
  `((id . ,tetris-mit-display-id) (kind . ,tetris-mit-display-kind)
    (rows . ,tetris-mit-rows) (cols . ,tetris-mit-cols) (fps . ,tetris-mit-fps)
    (protocol . ,tetris-mit-protocol) (version . ,tetris-mit-protocol-version)
    (transports . ["tcp"])))

(defun tetris-mit-display-identity-batch ()
  "Batch entry point: print `tetris-mit-display-identity' as JSON."
  (princ (concat (json-encode (tetris-mit-display-identity)) "\n")))

;;;; Grid

(defun tetris-mit-display--face (packed)
  "The cached face spec for the packed color PACKED."
  (or (gethash packed tetris-mit-display--faces)
      (puthash packed (list :background (format "#%06x" packed))
               tetris-mit-display--faces)))

(defun tetris-mit-display--init-grid ()
  "Build the 17x9 grid of cell overlays in the current buffer, all black."
  (let ((inhibit-read-only t)
        (overlays (make-vector tetris-mit-display--cells nil))
        (i 0))
    (delete-all-overlays)
    (erase-buffer)
    (dotimes (_row tetris-mit-rows)
      (dotimes (_col tetris-mit-cols)
        (let ((start (point)))
          (insert tetris-mit-cell-string)
          (let ((overlay (make-overlay start (point))))
            (overlay-put overlay 'face (tetris-mit-display--face 0))
            (aset overlays i overlay)
            (setq i (1+ i)))))
      (insert "\n"))
    (insert "\n")
    (setq tetris-mit-display--status (point-marker))
    (setq tetris-mit-display--overlays overlays
          tetris-mit-display--colors (make-vector tetris-mit-display--cells 0)
          tetris-mit-display--frame-no nil
          tetris-mit-display--frames 0
          tetris-mit-display--changed 0)
    (tetris-mit-display--update-status)
    (goto-char (point-min))))

(defun tetris-mit-display--update-status ()
  "Rewrite the status line below the grid."
  (let ((inhibit-read-only t))
    (save-excursion
      (delete-region tetris-mit-display--status (point-max))
      (goto-char tetris-mit-display--status)
      (insert (tetris-mit-status-string
               tetris-mit-display--state
               (and tetris-mit-display-show-frame-number tetris-mit-display--frame-no)
               (format "%s%s" tetris-mit-display-id
                       (if tetris-mit-display--note
                           (concat ", " tetris-mit-display--note)
                         "")))
              "\n"))))

(define-derived-mode tetris-mit-display-mode special-mode "Tetris-MIT-Display"
  "A 17x9 facade display: one overlay per window, colored by frames.
As a controller, the keys of `tetris-mit-remote-bindings' play.

\\{tetris-mit-display-mode-map}"
  (setq-local truncate-lines t)
  (setq-local cursor-type nil)
  (setq-local show-trailing-whitespace nil)
  (buffer-disable-undo)
  (tetris-mit-display--init-grid)
  (add-hook 'kill-buffer-hook #'tetris-mit-display-stop nil t))

(let ((map tetris-mit-display-mode-map))
  (dolist (binding tetris-mit-remote-bindings)
    (define-key map (kbd (car binding)) (tetris-mit--action-command (cdr binding))))
  (define-key map "q" #'tetris-mit-display-quit)
  (define-key map "S" #'tetris-mit-display-snapshot))

(defun tetris-mit-display-buffer ()
  "The display buffer, created and set up if needed."
  (or (get-buffer tetris-mit-display-buffer-name)
      (with-current-buffer (get-buffer-create tetris-mit-display-buffer-name)
        (tetris-mit-display-mode)
        (current-buffer))))

;;;; Showing frames

(defun tetris-mit-display--valid-p (rows)
  "Non-nil if ROWS is a 17x9 frame of [R G B] integers in 0..255.
The check allocates nothing."
  (and (vectorp rows) (= (length rows) tetris-mit-rows)
       (catch 'bad
         (dotimes (r tetris-mit-rows)
           (let ((row (aref rows r)))
             (unless (and (vectorp row) (= (length row) tetris-mit-cols))
               (throw 'bad nil))
             (dotimes (c tetris-mit-cols)
               (let ((cell (aref row c)))
                 (unless (and (vectorp cell) (= (length cell) 3)
                              (natnump (aref cell 0)) (<= (aref cell 0) 255)
                              (natnump (aref cell 1)) (<= (aref cell 1) 255)
                              (natnump (aref cell 2)) (<= (aref cell 2) 255))
                   (throw 'bad nil))))))
         t)))

(defvar tetris-mit-display-frame-functions nil
  "Functions called with (ROWS FRAME-NO) after each frame is shown.
`tetris-mit-display-source-game' uses it to forward the display's
frames to a relay.")

(defun tetris-mit-display-show (rows &optional frame-no state buffer)
  "Show the frame ROWS on the display in BUFFER (default: the display buffer).
ROWS is the `rows' payload of a protocol frame message: a vector of
17 rows, top first, each a vector of 9 [R G B] vectors of integers in
0..255.  FRAME-NO, if non-nil, is the frame's number.  STATE, if
non-nil, is a state alist (score, level, lines, phase) for the status
line.  Only the overlays whose color changed are repainted, and
nothing is allocated for them.  Return the number repainted.  Callers
pace themselves; SPEC §2.3 allows at most 30 frames per second."
  (with-current-buffer (or buffer (tetris-mit-display-buffer))
    (unless (tetris-mit-display--valid-p rows)
      (signal 'wrong-type-argument (list 'tetris-mit-frame rows)))
    (let ((overlays tetris-mit-display--overlays)
          (colors tetris-mit-display--colors)
          (changed 0)
          (i 0))
      (dotimes (r tetris-mit-rows)
        (let ((row (aref rows r)))
          (dotimes (c tetris-mit-cols)
            (let* ((cell (aref row c))
                   (packed (logior (ash (aref cell 0) 16) (ash (aref cell 1) 8)
                                   (aref cell 2))))
              (unless (eq packed (aref colors i))
                (aset colors i packed)
                (overlay-put (aref overlays i) 'face
                             (tetris-mit-display--face packed))
                (setq changed (1+ changed))))
            (setq i (1+ i)))))
      (setq tetris-mit-display--changed changed
            tetris-mit-display--frame-no frame-no
            tetris-mit-display--frames (1+ tetris-mit-display--frames))
      (cond ((and state (not (equal state tetris-mit-display--state)))
             (setq tetris-mit-display--state state)
             (tetris-mit-display--update-status))
            ((and tetris-mit-display-show-frame-number frame-no)
             (tetris-mit-display--update-status)))
      (run-hook-with-args 'tetris-mit-display-frame-functions rows frame-no)
      changed)))

(defun tetris-mit-display-rows (&optional buffer)
  "The frame on the display in BUFFER now, as fresh ROWS."
  (with-current-buffer (or buffer (tetris-mit-display-buffer))
    (let ((rows (make-vector tetris-mit-rows nil))
          (i 0))
      (dotimes (r tetris-mit-rows)
        (let ((row (make-vector tetris-mit-cols nil)))
          (dotimes (c tetris-mit-cols)
            (let ((packed (aref tetris-mit-display--colors i)))
              (aset row c (vector (ash packed -16) (logand (ash packed -8) 255)
                                  (logand packed 255))))
            (setq i (1+ i)))
          (aset rows r row)))
      rows)))

;;;; Frame sources

(defun tetris-mit-display--stop-provider ()
  "Stop polling the frame provider."
  (when (timerp tetris-mit-display--timer)
    (cancel-timer tetris-mit-display--timer))
  (setq tetris-mit-display--timer nil))

(defun tetris-mit-display--pull (buffer provider)
  "Ask PROVIDER for a frame, and show it in BUFFER."
  (when (buffer-live-p buffer)
    (with-current-buffer buffer
      (condition-case err
          (let ((frame (funcall provider)))
            (cond ((null frame))
                  ((vectorp frame) (tetris-mit-display-show frame nil nil buffer))
                  (t (tetris-mit-display-show (alist-get 'rows frame)
                                              (alist-get 'frame_no frame)
                                              (alist-get 'state frame) buffer))))
        (error
         (tetris-mit-display--stop-provider)
         (message "tetris-mit-display: provider stopped: %s"
                  (error-message-string err)))))))

(defun tetris-mit-display-set-provider (provider &optional buffer)
  "Poll PROVIDER for frames 30 times a second, and show them in BUFFER.
PROVIDER is a function of no arguments, for example a local engine
such as impl/elisp's.  It returns one of:
- nil, when there is no new frame;
- ROWS, a frame as for `tetris-mit-display-show';
- an alist shaped like a protocol frame message:
  ((rows . ROWS) (frame_no . N) (state . STATE)), where frame_no and
  state are optional.
A nil PROVIDER stops polling.  An error in PROVIDER also stops polling.
Pushing frames with `tetris-mit-display-show' works as well."
  (with-current-buffer (or buffer (tetris-mit-display-buffer))
    (tetris-mit-display--stop-provider)
    (when provider
      (setq tetris-mit-display--note "local provider")
      (tetris-mit-display--update-status)
      (setq tetris-mit-display--timer
            (run-at-time 0 (/ 1.0 tetris-mit-fps) #'tetris-mit-display--pull
                         (current-buffer) provider)))
    (current-buffer)))

(defun tetris-mit-display--receive (msg)
  "Handle MSG from the server in the display buffer."
  (pcase (alist-get 'type msg)
    ("frame" (tetris-mit-display-show (alist-get 'rows msg) (alist-get 'frame_no msg)))
    ("state"
     (setq tetris-mit-display--state msg)
     (tetris-mit-display--update-status))
    ("hello" (setq tetris-mit-display--server msg))
    ("error"
     (setq tetris-mit-display--last-error msg)
     (message "tetris-mit-display: %s: %s" (alist-get 'code msg)
              (alist-get 'message msg)))
    ("closed"
     (setq tetris-mit-display--process nil
           tetris-mit--process nil
           tetris-mit-display--note "disconnected")
     (tetris-mit-display--update-status))))

(defun tetris-mit-display-connect (&optional host port role seed buffer)
  "Show the frames of the protocol server at HOST:PORT on the display.
ROLE is \"viewer\" (the default, for engine or display servers) or
\"controller\" (engine servers only; the display's keys then play, and
SEED, if non-nil, chooses the session seed).  The connection goes
through `tetris-mit-resolve-endpoint', so an outer reservation
gatekeeper can front it.  Return the display buffer."
  (interactive
   (list (read-string "Host: " tetris-mit-host)
         (read-number "Port: " tetris-mit-port)
         (completing-read "Role: " '("viewer" "controller") nil t nil nil "viewer")))
  (let ((buffer (or buffer (tetris-mit-display-buffer)))
        (host (or host tetris-mit-host))
        (port (or port tetris-mit-port))
        (role (or role "viewer")))
    (with-current-buffer buffer
      (tetris-mit-display-disconnect)
      (let* ((target (tetris-mit-resolve-endpoint host port role
                                                  (tetris-mit-display-identity)))
             (proc (tetris-mit-open
                    "tetris-mit-display" (car target) (cdr target) role
                    (lambda (p msg)
                      (when (buffer-live-p buffer)
                        (with-current-buffer buffer
                          (when (eq p tetris-mit-display--process)
                            (tetris-mit-display--receive msg)))))
                    `((client . ,(format "tetris-mit-display.el; display=%s; kind=%s"
                                         tetris-mit-display-id tetris-mit-display-kind))
                      ,@(and seed `((seed . ,seed)))))))
        (setq tetris-mit-display--process proc
              tetris-mit-display--role role
              tetris-mit--process (and (equal role "controller") proc)
              tetris-mit-display--note (format "%s of %s:%s" role host port))
        (tetris-mit-display--update-status)))
    buffer))

(defun tetris-mit-display-disconnect ()
  "Close the display's server connection."
  (interactive)
  (let ((proc tetris-mit-display--process))
    (setq tetris-mit-display--process nil
          tetris-mit--process nil)
    (when (process-live-p proc)
      (delete-process proc))))

(defun tetris-mit-display-stop ()
  "Stop every frame source of the display."
  (interactive)
  (tetris-mit-display--stop-provider)
  (tetris-mit-display-disconnect))

(defun tetris-mit-display-quit ()
  "Stop the display and quit its window."
  (interactive)
  (tetris-mit-display-stop)
  (quit-window))

;;;###autoload
(defun tetris-mit-display (&optional host port role)
  "Show a 17x9 facade display, fed by the server at HOST:PORT as ROLE.
Without a prefix argument, connect to `tetris-mit-host' and
`tetris-mit-port' as a viewer.  With one, ask for the host, the port
and the role."
  (interactive
   (if current-prefix-arg
       (list (read-string "Host: " tetris-mit-host)
             (read-number "Port: " tetris-mit-port)
             (completing-read "Role: " '("viewer" "controller") nil t nil nil
                              "viewer"))
     (list tetris-mit-host tetris-mit-port "viewer")))
  (let ((buffer (tetris-mit-display-connect host port role)))
    (pop-to-buffer-same-window buffer)
    buffer))

;;;; Snapshots

(defun tetris-mit-display-ansi (rows)
  "ROWS as ANSI truecolor text, with a final newline.
Each window is two spaces on a 24-bit background, byte for byte as
tetris_sim.ansi.frame_to_ansi writes it."
  (concat (mapconcat
           (lambda (row)
             (concat (mapconcat (lambda (cell)
                                  (format "\e[48;2;%d;%d;%dm  "
                                          (aref cell 0) (aref cell 1) (aref cell 2)))
                                row "")
                     "\e[0m"))
           rows "\n")
          "\n"))

(defun tetris-mit-display-text (rows)
  "ROWS as 17 lines of 9 SPEC palette codes (\"?\" for other colors)."
  (mapconcat
   (lambda (row)
     (concat (mapconcat
              (lambda (cell)
                (or (car (cl-find-if (lambda (entry) (equal (cdr entry) (append cell nil)))
                                     tetris-mit-palette))
                    "?"))
              row "")
             "\n"))
   rows ""))

(defun tetris-mit-display-snapshot-data (&optional buffer)
  "The display in BUFFER as a snapshot alist, as written to the .json file."
  (with-current-buffer (or buffer (tetris-mit-display-buffer))
    (let ((rows (tetris-mit-display-rows))
          (state tetris-mit-display--state))
      `((format . "17x9-tetris-snapshot")
        (spec_version . 1)
        (display_id . ,tetris-mit-display-id)
        (display_kind . ,tetris-mit-display-kind)
        (frame_no . ,tetris-mit-display--frame-no)
        (digest . ,(tetris-mit-frame-digest rows))
        (rows . ,rows)
        (state . ,(and state
                       (cl-loop for key in '(score level lines high_score phase)
                                when (assq key state) collect (assq key state))))))))

(defun tetris-mit-display-snapshot (file &optional buffer)
  "Write the display in BUFFER as FILE.json, FILE.ans and FILE.txt.
The .json holds the RGB cells (`rows', as in a protocol frame
message), frame_no and the SPEC §9.4 digest.  The .ans holds ANSI
truecolor blocks, and the .txt holds palette codes.  A .json, .ans or
.txt extension on FILE is dropped.  Return the digest."
  (interactive (list (read-file-name "Snapshot (base name): ")))
  (let* ((base (replace-regexp-in-string "\\.\\(json\\|ans\\|txt\\)\\'" ""
                                         (expand-file-name file)))
         (data (tetris-mit-display-snapshot-data buffer))
         (rows (alist-get 'rows data))
         (coding-system-for-write 'utf-8-unix))
    (with-temp-file (concat base ".json")
      (insert (json-encode data) "\n"))
    (with-temp-file (concat base ".ans")
      (insert (tetris-mit-display-ansi rows)))
    (with-temp-file (concat base ".txt")
      (insert (tetris-mit-display-text rows)))
    (when (called-interactively-p 'interactive)
      (message "Snapshot %s.{json,ans,txt}, digest %s" base (alist-get 'digest data)))
    (alist-get 'digest data)))

(defun tetris-mit-display--sorted-events (events)
  "EVENTS, a vector of [FRAME ACTION DOWN], stably sorted by frame."
  (vconcat (sort (append events nil) (lambda (a b) (< (aref a 0) (aref b 0))))))

(defun tetris-mit-display-snapshot-run (seed frames events out &optional host port)
  "Snapshot frame FRAMES-1 of the game from SEED with EVENTS to OUT.
Play EVENTS (a vector of [FRAME ACTION DOWN]) for FRAMES frames on a
lockstep engine server, showing every frame on the display.  Then
write the snapshot OUT.{json,ans,txt}.  Without PORT, start a private
server with `tetris-mit-python'.  The same arguments always give the
same snapshot.  Return a plist (:digest :frame-no :base)."
  (let* ((server (unless port
                   (tetris-mit-start-server "--mode" "engine" "--clock" "lockstep")))
         (buffer (tetris-mit-display-buffer)))
    (with-current-buffer buffer
      (tetris-mit-display-stop)
      (tetris-mit-display--init-grid)
      (setq tetris-mit-display--state nil
            tetris-mit-display--note (format "seed %d" seed)))
    (unwind-protect
        (let ((result (tetris-mit-lockstep-run
                       (or host "127.0.0.1") (or port (cdr server)) seed frames
                       (tetris-mit-display--sorted-events events)
                       (lambda (msg)
                         (tetris-mit-display-show (alist-get 'rows msg)
                                                  (alist-get 'frame_no msg) nil buffer))
                       :on-state (lambda (msg)
                                   (with-current-buffer buffer
                                     (setq tetris-mit-display--state msg)
                                     (tetris-mit-display--update-status)))
                       :name "tetris-mit-snapshot")))
          (when (plist-get result :error)
            (error "tetris-mit-display: %s" (plist-get result :error)))
          (list :digest (tetris-mit-display-snapshot out buffer)
                :frame-no (buffer-local-value 'tetris-mit-display--frame-no buffer)
                :base (replace-regexp-in-string "\\.\\(json\\|ans\\|txt\\)\\'" ""
                                                (expand-file-name out))))
      (when server (tetris-mit-stop-server server)))))

(defun tetris-mit-display-snapshot-batch ()
  "Batch entry point for headless, reproducible snapshots.
  emacs --batch -l contrib/emacs/tetris-mit-display.el \\
        -f tetris-mit-display-snapshot-batch SEED EVENTS OUT \\
        [--frames N] [--host H --port P]
EVENTS is a JSON file: either an array of [frame, action, down]
entries, or an object with `events' and, optionally, `frames' (so a
conformance trace works).  \"-\" means no events.  FRAMES defaults to
the object's `frames', then to the last event's frame plus 1, then
to 1.  Write OUT.json, OUT.ans and OUT.txt, which hold the last frame,
and print its digest.  Exit with 0 on success and 2 on error."
  (let (args frames host port)
    (while command-line-args-left
      (let ((arg (pop command-line-args-left)))
        (cond ((equal arg "--frames")
               (setq frames (string-to-number (or (pop command-line-args-left) ""))))
              ((equal arg "--host") (setq host (pop command-line-args-left)))
              ((equal arg "--port")
               (setq port (string-to-number (or (pop command-line-args-left) ""))))
              (t (push arg args)))))
    (setq args (nreverse args))
    (unless (and (= (length args) 3) (string-match-p "\\`[0-9]+\\'" (car args)))
      (princ "usage: emacs --batch -l tetris-mit-display.el \
-f tetris-mit-display-snapshot-batch SEED EVENTS OUT [--frames N] [--host H --port P]\n")
      (kill-emacs 2))
    (condition-case err
        (let* ((seed (string-to-number (nth 0 args)))
               (spec (if (member (nth 1 args) '("-" "none"))
                         []
                       (tetris-mit-read-trace (expand-file-name (nth 1 args)))))
               (events (if (vectorp spec) spec (or (alist-get 'events spec) [])))
               (frames (or frames
                           (and (consp spec) (alist-get 'frames spec))
                           (and (> (length events) 0)
                                (1+ (cl-reduce #'max events
                                               :key (lambda (e) (aref e 0)))))
                           1))
               (result (tetris-mit-display-snapshot-run seed frames events
                                                        (nth 2 args) host port)))
          (princ (format "snapshot %s.{json,ans,txt}: seed %d, frame %d, digest %s\n"
                         (plist-get result :base) seed (plist-get result :frame-no)
                         (plist-get result :digest)))
          (kill-emacs 0))
      (error (princ (format "tetris-mit-display-snapshot-batch: %s\n"
                            (error-message-string err)))
             (kill-emacs 2)))))

(provide 'tetris-mit-display)

;;; tetris-mit-display.el ends here
