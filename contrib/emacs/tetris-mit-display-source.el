;;; tetris-mit-display-source.el --- Emacs as a frame source for a reserved display  -*- lexical-binding: t; -*-

;; Copyright (C) 2026 17x9-Tetris contributors

;; Version: 0.2.1
;; Package-Requires: ((emacs "28.1"))
;; Keywords: games
;; URL: https://github.com/aygp-dr/17x9-Tetris

;; This file is not part of GNU Emacs.

;;; Commentary:

;; This is the source side of the user's display protocol, the spec
;; wal.sh/tools/display v0.2.1.  It is pinned verbatim at
;; /scratch/work/tetris-parallel/inputs/wal-sh-display-0.2.1/ (spec.md
;; and capabilities.json), and docs/PROTOCOL.md §5.4 cites it.  Emacs
;; opens a WebSocket to a relay and sends a `reserve' naming the display
;; and the frame format.  Once `granted' arrives, with the grid (w x h),
;; the fps, the format and the display's 16-entry palette, Emacs sends
;; frames.  It sends `renew' when it is idle, and `release' on quit or
;; kill-buffer.
;;
;; The wire carries palette indices, not colours:
;; - a `pal16' frame (the native format) is a binary message of w*h
;;   bytes, one palette index 0..15 per cell, row-major, with row 0 at the
;;   top and column 0 at the left.  An optional 2-byte big-endian sequence
;;   number may precede it: the frame count modulo 65536;
;; - a `hex' frame is a text message of h lines of w lower-case hex
;;   digits, each ending in LF.
;; `rgb24' is for relays and shims only, and a source never sends it.
;;
;; Quantization belongs to the source (the spec's NR-QUANT).  Each colour
;; maps to the nearest of the 16 entries that `granted' announces, by
;; squared sRGB distance, with ties to the lower index.  The map is
;; cached per colour and per lease, so the game's ten SPEC colours
;; become a direct colour-to-index table after the first frame.  Cells
;; outside the frame are index 0, unlit.
;;
;; There are two frame sources:
;; - `tetris-mit-display-source-game' sends our game frames, whatever the
;;   overlay display (`tetris-mit-display') is showing, whether they come
;;   from the engine server or from an in-process provider.
;; - `tetris-mit-display-source-tetris' sends stock tetris.el's playing
;;   field, from its gamegrid, placed on the reserved display's w x h
;;   grid.  For example:
;;     emacs -nw -Q --eval \
;;       '(progn (setq tetris-width 10 tetris-height 12) (tetris))'
;;   then M-x tetris-mit-display-source-tetris RET trs80.
;;
;; Frames are paced to the display's fps.  A frame that arrives too
;; early replaces any frame still waiting for the next slot, so the
;; latest frame wins and nothing queues.
;;
;; The lease state is a chain: idle -> reserving -> granted -> released.
;; `busy' and `lost' (a `not-holder' error) are terminal branches, and
;; `closed' can happen at any point.  The relay's other error reasons,
;; `bad-frame-length', `bad-format', `rate' and `unknown-op', are
;; recorded and counted, and the lease is kept.
;;
;; This repository's default display is `green-building' (9 x 17, the
;; facade); the spec's own default is `cga40'.
;;
;; The WebSocket transport is websocket.el, from GNU ELPA or MELPA
;; (GPL-3.0-or-later; M-x package-install RET websocket).  Emacs has no
;; built-in client.  It is required only when a connection is opened,
;; and the tests use a fake link instead.  Only loopback URLs are
;; allowed unless `tetris-mit-display-source-allow-remote' is set, so
;; nothing is published to a live site by accident.

;;; Code:

(require 'cl-lib)
(require 'json)
(require 'url-parse)
(require 'tetris-mit
         (let ((dir (file-name-directory
                     (or load-file-name (bound-and-true-p byte-compile-current-file)
                         buffer-file-name default-directory))))
           (locate-file "tetris-mit" (list dir) (get-load-suffixes))))

(declare-function websocket-open "ext:websocket")
(declare-function websocket-send-text "ext:websocket")
(declare-function websocket-send "ext:websocket")
(declare-function websocket-close "ext:websocket")
(declare-function websocket-frame-opcode "ext:websocket")
(declare-function websocket-frame-text "ext:websocket")
(declare-function make-websocket-frame "ext:websocket")
(declare-function tetris-mit-display-rows "tetris-mit-display")
(defvar tetris-mit-display-frame-functions)

(defgroup tetris-mit-display-source nil
  "Emacs as a frame source for a reserved display."
  :group 'tetris-mit
  :prefix "tetris-mit-display-source-")

(defcustom tetris-mit-display-source-url "ws://127.0.0.1:8765/tools/display/ws"
  "WebSocket URL of the display relay.
The default is a local mock relay: contrib/displays,
\"python -m demo relay --port 8765\"."
  :type 'string)

(defcustom tetris-mit-display-source-allow-remote nil
  "Non-nil allows relay URLs whose host is not loopback.
This is off by default, so a relay on a live site is only ever reached
on purpose."
  :type 'boolean)

(defcustom tetris-mit-display-source-display "green-building"
  "The display to reserve: a preset name of the spec, such as cga40 or trs80.
This repository's default is green-building, the 9 x 17 facade.  The
spec's default display is cga40."
  :type 'string)

(defcustom tetris-mit-display-source-format "pal16"
  "The frame format to reserve.
\"pal16\" (binary, one byte per cell) is the spec's native format;
\"hex\" (text, one hex digit per cell) carries the same information."
  :type '(choice (const "pal16") (const "hex")))

(defcustom tetris-mit-display-source-name nil
  "The source name sent in `reserve', or nil for USER@HOST."
  :type '(choice (const :tag "user@host" nil) string))

(defcustom tetris-mit-display-source-ttl 300
  "Lease time to live, in seconds.  The relay allows at most 900."
  :type 'natnum)

(defcustom tetris-mit-display-source-renew-interval nil
  "Idle seconds before a `renew' is sent, or nil for a third of the ttl."
  :type '(choice (const :tag "ttl/3" nil) number))

(defcustom tetris-mit-display-source-sequence nil
  "Non-nil prefixes each pal16 frame with a 2-byte big-endian sequence number.
The number is the count of frames sent, modulo 65536, so 65535 is
followed by 0.  A relay that applies the spec's rule literally (a
sequence lower than the last accepted one is dropped with `rate') drops
the frames after the wrap until the lease ends.  The hex format has no
prefix."
  :type 'boolean)

(defconst tetris-mit-display-source-spec-version "0.2.1"
  "The version of wal.sh/tools/display this source follows.")

(defconst tetris-mit-display-source-profiles
  '(("cga40" 40 25) ("tetris" 10 20) ("green-building" 9 17) ("dc32" 10 18)
    ("gameboy" 10 18) ("trs80" 10 12) ("c64" 10 20) ("ws2812" 16 16)
    ("hub75" 64 32) ("blinkenlights" 18 8) ("arcade" 20 26) ("remote" 9 17))
  "The presets of the display spec v0.2.1, as (NAME W H), in its order.
They are informative only: the grid used is the one in `granted'.")

(defconst tetris-mit-display-source-cga
  ["#000000" "#0000AA" "#00AA00" "#00AAAA" "#AA0000" "#AA00AA" "#AA5500" "#AAAAAA"
   "#555555" "#5555FF" "#55FF55" "#55FFFF" "#FF5555" "#FF55FF" "#FFFF55" "#FFFFFF"]
  "The spec's default palette, cga: the IBM PC CGA sixteen.
It is used only when `granted' announces no valid palette.")

(defconst tetris-mit-display-source-formats '("pal16" "hex")
  "The frame formats a source sends.")

(defconst tetris-mit-display-source-max-ttl 900 "The relay's ttl limit.")

(cl-defstruct (tetris-mit-display-source-link
               (:constructor tetris-mit-display-source-make-link))
  "A transport: functions to send text and binary messages, and to close."
  send-text send-binary close)

(cl-defstruct (tetris-mit-display-source
               (:constructor tetris-mit-display-source--make))
  "One source session: a lease on one display."
  link (state 'idle) name display ttl (format "pal16") (w 0) (h 0) (fps 30)
  palette colors lease expires holder last-error (rejected 0)
  (last-sent 0.0) (last-activity 0.0) (seq 0) (sent 0) (dropped 0)
  pending timer renew-timer provider)

(defvar tetris-mit-display-source--sources nil "Live source sessions.")

(defvar-local tetris-mit-display-source--current nil
  "The source session of this tetris buffer.")

(defvar tetris-mit-display-source--game nil
  "The source session that follows the overlay display.")

;;;; Messages

(defun tetris-mit-display-source--default-name ()
  "USER@HOST, with a short host name."
  (format "%s@%s" (user-login-name) (car (split-string (system-name) "\\."))))

(defun tetris-mit-display-source-reserve-message (name display ttl &optional format)
  "The `reserve' text for NAME, DISPLAY, TTL and FORMAT (default \"pal16\").
TTL is clamped to 1..900."
  (json-encode `((op . "reserve") (name . ,name) (display . ,display)
                 (ttl . ,(max 1 (min tetris-mit-display-source-max-ttl ttl)))
                 (format . ,(or format "pal16")))))

(defun tetris-mit-display-source--send-text (source alist)
  "Send ALIST to SOURCE's relay as a JSON text message."
  (funcall (tetris-mit-display-source-link-send-text
            (tetris-mit-display-source-link source))
           (json-encode alist)))

;;;; Palette and quantization

(defun tetris-mit-display-source-hex-rgb (hex)
  "HEX, a \"#RRGGBB\" colour, as an [R G B] vector."
  (vector (string-to-number (substring hex 1 3) 16)
          (string-to-number (substring hex 3 5) 16)
          (string-to-number (substring hex 5 7) 16)))

(defun tetris-mit-display-source-palette-rgb (palette)
  "PALETTE, 16 \"#RRGGBB\" colours (a vector or a list), as 16 [R G B] vectors.
Anything else gives the cga palette."
  (let ((entries (append palette nil)))
    (vconcat (mapcar #'tetris-mit-display-source-hex-rgb
                     (if (and (= (length entries) 16)
                              (cl-every (lambda (c)
                                          (and (stringp c)
                                               (string-match-p "\\`#[[:xdigit:]]\\{6\\}\\'" c)))
                                        entries))
                         entries
                       (append tetris-mit-display-source-cga nil))))))

(defun tetris-mit-display-source-quantize (rgb palette)
  "The index of the entry of PALETTE nearest to the colour RGB.
PALETTE is a sequence of 16 [R G B]; RGB is (R G B) or [R G B].  The
distance is squared Euclidean in sRGB, and ties go to the lower index
\(the spec's Source recipes)."
  (let ((best 0) (best-d nil))
    (dotimes (i (length palette))
      (let* ((c (elt palette i))
             (d (+ (expt (- (elt rgb 0) (elt c 0)) 2)
                   (expt (- (elt rgb 1) (elt c 1)) 2)
                   (expt (- (elt rgb 2) (elt c 2)) 2))))
        (when (or (null best-d) (< d best-d))
          (setq best i best-d d))))
    best))

(defun tetris-mit-display-source--index (cell palette cache)
  "The palette index of CELL, [R G B], memoized in CACHE if non-nil."
  (if (null cache)
      (tetris-mit-display-source-quantize cell palette)
    (let ((key (logior (ash (elt cell 0) 16) (ash (elt cell 1) 8) (elt cell 2))))
      (or (gethash key cache)
          (puthash key (tetris-mit-display-source-quantize cell palette) cache)))))

;;;; Frames

(defun tetris-mit-display-source-indices (rows w h palette &optional cache)
  "ROWS centered on a W x H canvas, as a W*H unibyte string of palette indices.
ROWS is a vector of rows (top first) of [R G B] vectors.  Each colour
becomes the index of the nearest entry of PALETTE, a sequence of 16
[R G B]; CACHE, a hash table, memoizes that per colour when non-nil.
Cells outside the frame are index 0, unlit.  A frame larger than the
canvas is cropped around its center.  Row 0 is at the top and column 0
at the left."
  (let* ((rh (length rows))
         (rw (if (> rh 0) (length (aref rows 0)) 0))
         (dy (/ (- h rh) 2))
         (dx (/ (- w rw) 2))
         (out (make-string (* w h) 0)))
    (dotimes (y h)
      (let* ((sy (- y dy))
             (row (and (>= sy 0) (< sy rh) (aref rows sy))))
        (when row
          (dotimes (x w)
            (let* ((sx (- x dx))
                   (cell (and (>= sx 0) (< sx rw) (aref row sx))))
              (when cell
                (aset out (+ (* y w) x)
                      (tetris-mit-display-source--index cell palette cache))))))))
    out))

(defun tetris-mit-display-source-encode-pal16 (cells &optional seq)
  "CELLS, a unibyte string of indices 0..15, as a pal16 frame.
With SEQ, prefix it with SEQ modulo 65536, as 2 bytes, big-endian."
  (when (cl-some (lambda (b) (> b 15)) cells)
    (error "A pal16 cell is an index from 0 to 15"))
  (if (null seq)
      cells
    (let ((s (mod seq 65536)))
      (concat (unibyte-string (ash s -8) (logand s 255)) cells))))

(defun tetris-mit-display-source-encode-hex (cells w h &optional blank)
  "CELLS, a string of W*H indices 0..15, as a hex frame of H lines.
Each line is W lower-case hex digits and an LF.  With BLANK, an empty
line ends the frame."
  (let ((digits "0123456789abcdef"))
    (concat (mapconcat (lambda (y)
                         (concat (cl-loop for x below w
                                          concat (string (aref digits
                                                               (aref cells (+ (* y w) x)))))
                                 "\n"))
                       (number-sequence 0 (1- h)) "")
            (if blank "\n" ""))))

(defun tetris-mit-display-source--hex-digit (char)
  "The value of the hex digit CHAR, or nil.  Upper case is accepted too."
  (cond ((<= ?0 char ?9) (- char ?0))
        ((<= ?a char ?f) (+ 10 (- char ?a)))
        ((<= ?A char ?F) (+ 10 (- char ?A)))))

(defun tetris-mit-display-source-decode (format data w h)
  "Decode the FORMAT frame DATA for a W x H display, as the spec's sink does.
FORMAT is \"pal16\" (DATA a unibyte string) or \"hex\" (DATA a string).
Return (:cells CELLS :seq SEQ), where CELLS is a unibyte string of W*H
indices and SEQ the sequence prefix or nil.  Or return (:error REASON),
where REASON is \"bad-frame-length\" or \"bad-format\".  The length is
checked before the contents.  A source uses this to check what it
sends; the tests use it for the spec's boundaries."
  (let ((n (* w h)))
    (pcase format
      ("pal16"
       (let ((len (length data)))
         (if (not (memql len (list n (+ n 2))))
             '(:error "bad-frame-length")
           (let ((cells (substring data (- len n))))
             (if (cl-some (lambda (b) (> b 15)) cells)
                 '(:error "bad-format")
               (list :cells (if (multibyte-string-p cells)
                                (apply #'unibyte-string (append cells nil))
                              cells)
                     :seq (and (= len (+ n 2))
                               (+ (* 256 (aref data 0)) (aref data 1)))))))))
      ("hex"
       (let* ((m (* h (1+ w)))
              (len (length data)))
         (cond ((not (memql len (list m (1+ m)))) '(:error "bad-frame-length"))
               ((and (= len (1+ m)) (/= (aref data m) ?\n)) '(:error "bad-format"))
               (t (let ((cells (make-string n 0)) (bad nil))
                    (dotimes (y h)
                      (let ((base (* y (1+ w))))
                        (unless (eq (aref data (+ base w)) ?\n) (setq bad t))
                        (dotimes (x w)
                          (let ((v (tetris-mit-display-source--hex-digit
                                    (aref data (+ base x)))))
                            (if v (aset cells (+ (* y w) x) v) (setq bad t))))))
                    (if bad '(:error "bad-format") (list :cells cells :seq nil)))))))
      (_ '(:error "bad-format")))))

;;;; Lease

(defun tetris-mit-display-source--check-url (url)
  "Signal a `user-error' unless URL is a ws(s) URL on loopback.
Any host is allowed when `tetris-mit-display-source-allow-remote' is set."
  (let* ((parsed (url-generic-parse-url url))
         (host (url-host parsed)))
    (unless (member (url-type parsed) '("ws" "wss"))
      (user-error "Not a WebSocket URL: %s" url))
    (unless (or tetris-mit-display-source-allow-remote
                (member host '("127.0.0.1" "localhost" "::1" "[::1]")))
      (user-error "Refusing non-loopback relay %s; set \
`tetris-mit-display-source-allow-remote' to reach it on purpose" host))))

(defun tetris-mit-display-source--websocket-link (url source)
  "A websocket.el link to URL that delivers its messages to SOURCE."
  (tetris-mit-display-source--check-url url)
  (unless (require 'websocket nil t)
    (user-error "tetris-mit-display-source needs websocket.el \
\(GPL-3.0+): M-x package-install RET websocket, from GNU ELPA or MELPA"))
  (let ((ws (websocket-open
             url
             :on-message (lambda (_ws frame)
                           (when (eq (websocket-frame-opcode frame) 'text)
                             (tetris-mit-display-source-receive
                              source (websocket-frame-text frame))))
             :on-close (lambda (_ws) (tetris-mit-display-source--closed source))
             :on-error (lambda (_ws type err)
                         (setf (tetris-mit-display-source-last-error source)
                               (format "%s: %S" type err))))))
    (tetris-mit-display-source-make-link
     :send-text (lambda (text) (websocket-send-text ws text))
     :send-binary (lambda (bytes)
                    (websocket-send ws (make-websocket-frame
                                        :opcode 'binary :payload bytes :completep t)))
     :close (lambda () (websocket-close ws)))))

(cl-defun tetris-mit-display-source-open (display &key url link name ttl format provider)
  "Open a source session for DISPLAY, and send the `reserve'.
URL is the relay (default `tetris-mit-display-source-url').  LINK, if
given, is a `tetris-mit-display-source-link' to use instead of a
WebSocket, as the tests do.  NAME, TTL and FORMAT default to the
options.  PROVIDER, if non-nil, is a function of no arguments that
returns the current frame as ROWS.  It is sent as soon as the lease is
granted, and whenever `tetris-mit-display-source-offer' offers it.
Return the session."
  (let ((format (or format tetris-mit-display-source-format)))
    (unless (member format tetris-mit-display-source-formats)
      (user-error "A source sends pal16 or hex frames, not %s" format))
    (let ((source (tetris-mit-display-source--make
                   :name (or name tetris-mit-display-source-name
                             (tetris-mit-display-source--default-name))
                   :display display
                   :ttl (max 1 (min tetris-mit-display-source-max-ttl
                                    (or ttl tetris-mit-display-source-ttl)))
                   :format format
                   :provider provider)))
      (setf (tetris-mit-display-source-link source)
            (or link (tetris-mit-display-source--websocket-link
                      (or url tetris-mit-display-source-url) source)))
      (push source tetris-mit-display-source--sources)
      (setf (tetris-mit-display-source-state source) 'reserving
            (tetris-mit-display-source-last-activity source) (float-time))
      (funcall (tetris-mit-display-source-link-send-text
                (tetris-mit-display-source-link source))
               (tetris-mit-display-source-reserve-message
                (tetris-mit-display-source-name source) display
                (tetris-mit-display-source-ttl source) format))
      source)))

(defun tetris-mit-display-source-receive (source text)
  "Handle the relay's text message TEXT for SOURCE."
  (let ((msg (condition-case nil (tetris-mit--json-parse text) (error nil))))
    (pcase (and (consp msg) (consp (car msg)) (alist-get 'op msg))
      ("granted"
       (setf (tetris-mit-display-source-state source) 'granted
             (tetris-mit-display-source-lease source) (alist-get 'lease msg)
             (tetris-mit-display-source-w source) (alist-get 'w msg)
             (tetris-mit-display-source-h source) (alist-get 'h msg)
             (tetris-mit-display-source-fps source) (or (alist-get 'fps msg) 30)
             (tetris-mit-display-source-format source)
             (or (car (member (alist-get 'format msg) tetris-mit-display-source-formats))
                 (tetris-mit-display-source-format source))
             (tetris-mit-display-source-palette source)
             (tetris-mit-display-source-palette-rgb (alist-get 'palette msg))
             (tetris-mit-display-source-colors source) (make-hash-table :test #'eql)
             (tetris-mit-display-source-expires source) (alist-get 'expires msg))
       (tetris-mit-display-source--start-renew source)
       (when (tetris-mit-display-source-provider source)
         (tetris-mit-display-source-offer source
                                          (tetris-mit-display-source-provider source))))
      ("busy"
       (setf (tetris-mit-display-source-state source) 'busy
             (tetris-mit-display-source-holder source) (alist-get 'holder msg)
             (tetris-mit-display-source-expires source) (alist-get 'expires msg))
       (message "Display %s is busy: held by %s" (tetris-mit-display-source-display source)
                (alist-get 'holder msg)))
      ("error"
       (let ((reason (alist-get 'reason msg)))
         (setf (tetris-mit-display-source-last-error source) reason)
         (cl-incf (tetris-mit-display-source-rejected source))
         (when (equal reason "not-holder")
           (setf (tetris-mit-display-source-state source) 'lost)
           (tetris-mit-display-source--cancel-timers source))
         (message "Display relay error: %s" reason))))))

(defun tetris-mit-display-source--closed (source)
  "The relay closed SOURCE's connection."
  (tetris-mit-display-source--cancel-timers source)
  (unless (eq (tetris-mit-display-source-state source) 'released)
    (setf (tetris-mit-display-source-state source) 'closed))
  (setq tetris-mit-display-source--sources
        (delq source tetris-mit-display-source--sources)))

(defun tetris-mit-display-source--cancel-timers (source)
  "Cancel SOURCE's frame and renew timers, and drop any waiting frame."
  (dolist (timer (list (tetris-mit-display-source-timer source)
                       (tetris-mit-display-source-renew-timer source)))
    (when (timerp timer) (cancel-timer timer)))
  (setf (tetris-mit-display-source-timer source) nil
        (tetris-mit-display-source-renew-timer source) nil
        (tetris-mit-display-source-pending source) nil))

(defun tetris-mit-display-source--renew-interval (source)
  "Idle seconds before SOURCE sends a `renew'."
  (or tetris-mit-display-source-renew-interval
      (max 1 (/ (tetris-mit-display-source-ttl source) 3.0))))

(defun tetris-mit-display-source--start-renew (source)
  "Start renewing SOURCE's lease whenever it is idle."
  (let ((interval (tetris-mit-display-source--renew-interval source)))
    (when (timerp (tetris-mit-display-source-renew-timer source))
      (cancel-timer (tetris-mit-display-source-renew-timer source)))
    (setf (tetris-mit-display-source-renew-timer source)
          (run-at-time interval interval #'tetris-mit-display-source--maybe-renew
                       source))))

(defun tetris-mit-display-source--maybe-renew (source)
  "Send a `renew' for SOURCE if it has been idle for a renew interval."
  (when (and (eq (tetris-mit-display-source-state source) 'granted)
             (>= (- (float-time) (tetris-mit-display-source-last-activity source))
                 (* 0.9 (tetris-mit-display-source--renew-interval source))))
    (tetris-mit-display-source--send-text source '((op . "renew")))
    (setf (tetris-mit-display-source-last-activity source) (float-time))))

(defun tetris-mit-display-source-stop (&optional source)
  "Release SOURCE's lease and close its connection.
Without SOURCE, stop this buffer's session, or else every session."
  (interactive)
  (let ((sources (cond (source (list source))
                       (tetris-mit-display-source--current
                        (list tetris-mit-display-source--current))
                       (t (copy-sequence tetris-mit-display-source--sources)))))
    (dolist (src sources)
      (tetris-mit-display-source--cancel-timers src)
      (when (memq (tetris-mit-display-source-state src) '(reserving granted))
        (ignore-errors (tetris-mit-display-source--send-text src '((op . "release")))))
      (setf (tetris-mit-display-source-state src) 'released)
      (ignore-errors
        (funcall (tetris-mit-display-source-link-close (tetris-mit-display-source-link src))))
      (setq tetris-mit-display-source--sources
            (delq src tetris-mit-display-source--sources))
      (when (eq src tetris-mit-display-source--game)
        (setq tetris-mit-display-source--game nil)
        (remove-hook 'tetris-mit-display-frame-functions
                     #'tetris-mit-display-source--game-frame)))
    (when (and source (eq source tetris-mit-display-source--current))
      (setq tetris-mit-display-source--current nil))
    (unless source (setq tetris-mit-display-source--current nil))
    (unless (cl-some (lambda (b) (buffer-local-value 'tetris-mit-display-source--current b))
                     (buffer-list))
      (advice-remove 'gamegrid-set-cell #'tetris-mit-display-source--after-set-cell))))

;;;; Pacing

(defun tetris-mit-display-source-frame (source rows)
  "ROWS as SOURCE's next frame: pal16 bytes, or hex text.
The grid and the palette are the ones `granted' announced.  With
`tetris-mit-display-source-sequence', a pal16 frame carries the
sequence number, which then advances modulo 65536."
  (let* ((w (tetris-mit-display-source-w source))
         (h (tetris-mit-display-source-h source))
         (cells (tetris-mit-display-source-indices
                 rows w h
                 (or (tetris-mit-display-source-palette source)
                     (tetris-mit-display-source-palette-rgb nil))
                 (tetris-mit-display-source-colors source))))
    (if (equal (tetris-mit-display-source-format source) "hex")
        (tetris-mit-display-source-encode-hex cells w h)
      (let ((seq (and tetris-mit-display-source-sequence
                      (tetris-mit-display-source-seq source))))
        (when seq
          (setf (tetris-mit-display-source-seq source) (logand (1+ seq) #xffff)))
        (tetris-mit-display-source-encode-pal16 cells seq)))))

(defun tetris-mit-display-source--send-now (source frame now)
  "Send FRAME (ROWS, or a function returning ROWS) from SOURCE at NOW."
  (let* ((rows (if (functionp frame) (funcall frame) frame))
         (data (tetris-mit-display-source-frame source rows))
         (link (tetris-mit-display-source-link source)))
    (funcall (if (equal (tetris-mit-display-source-format source) "hex")
                 (tetris-mit-display-source-link-send-text link)
               (tetris-mit-display-source-link-send-binary link))
             data)
    (setf (tetris-mit-display-source-last-sent source) now
          (tetris-mit-display-source-last-activity source) now)
    (cl-incf (tetris-mit-display-source-sent source))))

(defun tetris-mit-display-source--flush (source)
  "Send SOURCE's waiting frame, if it still holds the lease."
  (setf (tetris-mit-display-source-timer source) nil)
  (let ((frame (tetris-mit-display-source-pending source)))
    (setf (tetris-mit-display-source-pending source) nil)
    (when (and frame (eq (tetris-mit-display-source-state source) 'granted))
      (tetris-mit-display-source--send-now source frame (float-time)))))

(defun tetris-mit-display-source-offer (source frame)
  "Offer FRAME to SOURCE: ROWS, or a function of no arguments returning ROWS.
If the fps allows a frame now, it is sent now.  Otherwise it replaces
any frame already waiting, and is sent at the next slot: the latest
frame wins, and nothing queues.  Without a granted lease, the frame is
dropped.  A function is only called when its frame is actually sent."
  (if (not (eq (tetris-mit-display-source-state source) 'granted))
      (cl-incf (tetris-mit-display-source-dropped source))
    (let* ((now (float-time))
           (next (+ (tetris-mit-display-source-last-sent source)
                    (/ 1.0 (tetris-mit-display-source-fps source)))))
      (if (and (>= now next) (not (tetris-mit-display-source-timer source)))
          (tetris-mit-display-source--send-now source frame now)
        (when (tetris-mit-display-source-pending source)
          (cl-incf (tetris-mit-display-source-dropped source)))
        (setf (tetris-mit-display-source-pending source) frame)
        (unless (tetris-mit-display-source-timer source)
          (setf (tetris-mit-display-source-timer source)
                (run-at-time (max 0 (- next now)) nil
                             #'tetris-mit-display-source--flush source)))))))

;;;; Source (b): stock tetris.el

(defun tetris-mit-display-source--cell-rgb (cell)
  "The RGB vector of the tetris.el gamegrid CELL, from `tetris-x-colors'."
  (if (and (integerp cell) (<= 0 cell 6))
      (let ((color (aref tetris-x-colors cell)))
        (vector (max 0 (min 255 (round (* 255 (aref color 0)))))
                (max 0 (min 255 (round (* 255 (aref color 1)))))
                (max 0 (min 255 (round (* 255 (aref color 2)))))))
    (vector 0 0 0)))

(defun tetris-mit-display-source-gamegrid-rows ()
  "The playing field of the current tetris.el buffer, as ROWS.
The value is `tetris-height' rows of `tetris-width' [R G B] cells.
Only the playing field is included, not the border, the next piece or
the score."
  (let ((rows (make-vector tetris-height nil)))
    (dotimes (y tetris-height)
      (let ((row (make-vector tetris-width nil)))
        (dotimes (x tetris-width)
          (aset row x (tetris-mit-display-source--cell-rgb
                       (gamegrid-get-cell (+ tetris-top-left-x x)
                                          (+ tetris-top-left-y y)))))
        (aset rows y row)))
    rows))

(defun tetris-mit-display-source--after-set-cell (&rest _)
  "After a gamegrid cell changes, offer this buffer's playing field."
  (let ((source tetris-mit-display-source--current))
    (when (and source (tetris-mit-display-source-provider source))
      (tetris-mit-display-source-offer source (tetris-mit-display-source-provider source)))))

(defun tetris-mit-display-source--read-display ()
  "Read a display preset name."
  (completing-read (format "Display (default %s): " tetris-mit-display-source-display)
                   (mapcar #'car tetris-mit-display-source-profiles) nil nil nil nil
                   tetris-mit-display-source-display))

;;;###autoload
(defun tetris-mit-display-source-tetris (&optional display url link)
  "Send this tetris.el buffer's playing field to the relay's DISPLAY.
Run it in a `tetris-mode' buffer.  Every gamegrid change offers the
field, at most at the display's fps.  The field is placed on the
display's w x h grid, from `granted', in its palette.  URL defaults
to `tetris-mit-display-source-url'.  LINK is for tests.  Killing the
buffer, or \\[tetris-mit-display-source-stop], releases the lease."
  (interactive (list (tetris-mit-display-source--read-display)))
  (unless (derived-mode-p 'tetris-mode)
    (user-error "Run this in a tetris buffer (M-x tetris)"))
  (when tetris-mit-display-source--current
    (tetris-mit-display-source-stop tetris-mit-display-source--current))
  (let* ((buffer (current-buffer))
         (source (tetris-mit-display-source-open
                  (or display tetris-mit-display-source-display)
                  :url url :link link
                  :provider (lambda ()
                              (with-current-buffer buffer
                                (tetris-mit-display-source-gamegrid-rows))))))
    (setq tetris-mit-display-source--current source)
    (advice-add 'gamegrid-set-cell :after #'tetris-mit-display-source--after-set-cell)
    (add-hook 'kill-buffer-hook #'tetris-mit-display-source-stop nil t)
    source))

;;;; Source (a): our game frames, through the overlay display

(defun tetris-mit-display-source--game-frame (_rows _frame-no)
  "Offer the overlay display's frame to the game source."
  (let ((source tetris-mit-display-source--game))
    (when source
      (tetris-mit-display-source-offer source (tetris-mit-display-source-provider source)))))

;;;###autoload
(defun tetris-mit-display-source-game (&optional display url link)
  "Send the overlay display's frames (`tetris-mit-display') to the relay's DISPLAY.
Whatever feeds the display is forwarded: the engine server (as viewer
or controller), or an in-process provider such as a local engine.  The
fps limit applies.  URL defaults to `tetris-mit-display-source-url'.
LINK is for tests."
  (interactive (list (tetris-mit-display-source--read-display)))
  (require 'tetris-mit-display)
  (when tetris-mit-display-source--game
    (tetris-mit-display-source-stop tetris-mit-display-source--game))
  (let ((source (tetris-mit-display-source-open
                 (or display tetris-mit-display-source-display)
                 :url url :link link
                 :provider (lambda () (tetris-mit-display-rows)))))
    (setq tetris-mit-display-source--game source)
    (add-hook 'tetris-mit-display-frame-functions #'tetris-mit-display-source--game-frame)
    source))

(provide 'tetris-mit-display-source)

;;; tetris-mit-display-source.el ends here
