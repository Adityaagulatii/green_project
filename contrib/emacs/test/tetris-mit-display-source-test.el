;;; tetris-mit-display-source-test.el --- ERT for tetris-mit-display-source.el  -*- lexical-binding: t; -*-

;;; Commentary:

;; The display source against wal.sh/tools/display v0.2.1: pal16 and hex
;; framing, quantization, the lease, pacing and the spec's boundaries.
;; websocket.el is not installed here, so these use a fake link and
;; nothing connects anywhere.  The displays kit's fixtures
;; (contrib/displays/contract/fixtures, or $TETRIS_MIT_DISPLAY_FIXTURES)
;; are checked when they are present, and skipped otherwise.
;;
;;   emacs --batch -Q -L contrib/emacs -L contrib/emacs/test -l ert \
;;     -l tetris-mit-display-source-test -f ert-run-tests-batch-and-exit

;;; Code:

(require 'ert)
(require 'cl-lib)
(require 'tetris-mit-display)
(require 'tetris-mit-display-source)
(require 'tetris-mit-test)

(defconst tetris-mit-dsrc-test--cga
  (tetris-mit-display-source-palette-rgb tetris-mit-display-source-cga)
  "The cga palette as 16 [R G B].")

(defun tetris-mit-dsrc-test--fake ()
  "A fake link, and a function that returns what was sent, oldest first.
Each sent item is (text STRING), (binary BYTES) or (close)."
  (let* ((sent nil)
         (link (tetris-mit-display-source-make-link
                :send-text (lambda (s) (push (list 'text s) sent))
                :send-binary (lambda (b) (push (list 'binary b) sent))
                :close (lambda () (push (list 'close) sent)))))
    (cons link (lambda () (reverse sent)))))

(defun tetris-mit-dsrc-test--texts (log)
  "The decoded JSON text messages in LOG."
  (cl-loop for item in log
           when (and (eq (car item) 'text) (string-prefix-p "{" (cadr item)))
           collect (tetris-mit--json-parse (cadr item))))

(defun tetris-mit-dsrc-test--binaries (log)
  "The binary messages in LOG."
  (cl-loop for item in log when (eq (car item) 'binary) collect (cadr item)))

(defun tetris-mit-dsrc-test--granted (w h &optional fps format palette)
  "A `granted' for a W x H display at FPS, in FORMAT, with PALETTE (cga)."
  (json-encode `((op . "granted") (lease . "L1") (w . ,w) (h . ,h)
                 (fps . ,(or fps 30)) (format . ,(or format "pal16"))
                 (palette . ,(or palette tetris-mit-display-source-cga))
                 (expires . 1789000000))))

(defun tetris-mit-dsrc-test--bytes (values)
  "VALUES, a sequence of integers in 0..255, as a unibyte string."
  (let ((out (make-string (length values) 0)) (i 0))
    (seq-doseq (v values) (aset out i v) (setq i (1+ i)))
    out))

(defmacro tetris-mit-dsrc-test--with-source (spec &rest body)
  "SPEC is (SOURCE LOG DISPLAY &rest OPEN-ARGS): open a faked source."
  (declare (indent 1))
  (let ((fake (make-symbol "fake")))
    `(let* ((,fake (tetris-mit-dsrc-test--fake))
            (,(car spec) (tetris-mit-display-source-open
                          ,(nth 2 spec) :link (car ,fake) :name "emacs@test"
                          ,@(nthcdr 3 spec)))
            (,(cadr spec) (cdr ,fake)))
       (unwind-protect (progn ,@body)
         (tetris-mit-display-source-stop ,(car spec))))))

;;;; Messages, defaults and quantization

(ert-deftest tetris-mit-dsrc-test-reserve-message ()
  (tetris-mit-dsrc-test--with-source (source log "trs80" :ttl 5000)
    (should (eq (tetris-mit-display-source-state source) 'reserving))
    (should (equal (car (tetris-mit-dsrc-test--texts (funcall log)))
                   '((op . "reserve") (name . "emacs@test") (display . "trs80")
                     (ttl . 900) (format . "pal16"))))
    (should-not (tetris-mit-dsrc-test--binaries (funcall log))))
  (tetris-mit-dsrc-test--with-source (source log "green-building" :format "hex")
    (should (equal (alist-get 'format (car (tetris-mit-dsrc-test--texts (funcall log))))
                   "hex")))
  (should-error (tetris-mit-display-source-open
                 "green-building" :link (car (tetris-mit-dsrc-test--fake)) :format "rgb24")
                :type 'user-error)
  (should (string-match-p "\\`[^@]+@[^@.]+\\'" (tetris-mit-display-source--default-name))))

(ert-deftest tetris-mit-dsrc-test-green-building-is-the-default ()
  "This repository reserves the facade by default; the spec's own default is cga40."
  (should (equal (default-value 'tetris-mit-display-source-display) "green-building"))
  (should (equal (default-value 'tetris-mit-display-source-format) "pal16"))
  (should (equal (assoc "green-building" tetris-mit-display-source-profiles)
                 '("green-building" 9 17)))
  (should (equal (assoc "cga40" tetris-mit-display-source-profiles) '("cga40" 40 25)))
  (should (= (length tetris-mit-display-source-profiles) 12))
  (tetris-mit-dsrc-test--with-source (source log tetris-mit-display-source-display)
    (should (equal (alist-get 'display (car (tetris-mit-dsrc-test--texts (funcall log))))
                   "green-building"))))

(ert-deftest tetris-mit-dsrc-test-quantize-spec-palette ()
  "The ten SPEC colours on cga, as the displays kit's Python `quantize' maps them."
  (pcase-dolist (`(,code . ,index)
                 '(("." . 0) ("W" . 15) ("I" . 11) ("J" . 1) ("L" . 6) ("O" . 14)
                   ("S" . 2) ("Z" . 4) ("T" . 5) ("G" . 0)))
    (should (equal (cons code (tetris-mit-display-source-quantize
                               (tetris-mit-palette-rgb code) tetris-mit-dsrc-test--cga))
                   (cons code index))))
  ;; Ties go to the lower index: [0 0 85] is halfway between entries 0 and
  ;; 1, and L's orange is as near to 6 as to 12 and 14.
  (should (= 0 (tetris-mit-display-source-quantize [0 0 85] tetris-mit-dsrc-test--cga)))
  (should (= 6 (tetris-mit-display-source-quantize [255 170 0] tetris-mit-dsrc-test--cga)))
  (should (equal (tetris-mit-display-source-palette-rgb '("#000000"))
                 tetris-mit-dsrc-test--cga)))

(ert-deftest tetris-mit-dsrc-test-indices-geometry ()
  "Row-major, row 0 at the top, column 0 at the left; centered, cropped; unlit outside."
  (let ((rows (tetris-mit-test--rows)))
    (tetris-mit-test--set rows 0 0 [255 0 0])      ; Z: index 4
    (tetris-mit-test--set rows 16 8 [0 0 255])     ; J: index 1
    (let ((cells (tetris-mit-display-source-indices rows 9 17 tetris-mit-dsrc-test--cga)))
      (should (= (length cells) 153))
      (should-not (multibyte-string-p cells))
      (should (= (aref cells 0) 4))
      (should (= (aref cells 152) 1))
      (should (= 2 (cl-count-if-not #'zerop cells))))
    ;; tetris, 10 x 20: centered with dx 0 and dy 1
    (let ((cells (tetris-mit-display-source-indices rows 10 20 tetris-mit-dsrc-test--cga)))
      (should (= (length cells) 200))
      (should (equal (substring cells 0 10) (make-string 10 0)))
      (should (= (aref cells 10) 4))
      (should (= (aref cells (+ (* 17 10) 8)) 1)))
    ;; trs80, 10 x 12, is shorter than 17: rows 2..13 are kept
    (let ((cells (tetris-mit-display-source-indices rows 10 12 tetris-mit-dsrc-test--cga)))
      (should (= (length cells) 120))
      (should (cl-every #'zerop cells))))
  (let ((cache (make-hash-table)))
    (tetris-mit-display-source-indices (tetris-mit-test--rows [255 170 0]) 9 17
                                       tetris-mit-dsrc-test--cga cache)
    (should (= (hash-table-count cache) 1))
    (should (= (gethash #xffaa00 cache) 6))))

;;;; Boundaries of the spec (the user's list)

(ert-deftest tetris-mit-dsrc-test-pal16-boundaries ()
  "pal16 at 9 x 17: w*h and w*h+2 decode; w*h-1, w*h+1 and w*h+3 do not.
Index 15 is a cell; 16 and 255 are not."
  (let* ((w 9) (h 17) (n 153)
         (cells (tetris-mit-dsrc-test--bytes (cl-loop for i below n collect (% i 16))))
         (decode (lambda (data) (tetris-mit-display-source-decode "pal16" data w h))))
    (should (equal (funcall decode cells) (list :cells cells :seq nil)))
    (should (equal (funcall decode (tetris-mit-display-source-encode-pal16 cells 258))
                   (list :cells cells :seq 258)))
    (dolist (len (list 0 (1- n) (1+ n) (+ n 3) (* 3 n)))
      (should (equal (list len (funcall decode (make-string len 0)))
                     (list len '(:error "bad-frame-length")))))
    (should (equal (funcall decode (make-string n 15)) (list :cells (make-string n 15) :seq nil)))
    (should (equal (funcall decode (make-string n 16)) '(:error "bad-format")))
    (dolist (byte '(16 255))
      (let ((one (copy-sequence cells)))
        (aset one 77 byte)
        (should (equal (list byte (funcall decode one)) (list byte '(:error "bad-format"))))))
    ;; the length is checked first (the kit's reading)
    (should (equal (funcall decode (make-string (1+ n) 16)) '(:error "bad-frame-length")))
    ;; the prefix bytes are a number, and may hold any value
    (should (equal (plist-get (funcall decode (concat (unibyte-string 255 255) cells)) :seq)
                   65535))
    (should-error (tetris-mit-display-source-encode-pal16 (make-string n 16)))))

(ert-deftest tetris-mit-dsrc-test-sequence-wraps ()
  "The 2-byte sequence prefix is the frame count modulo 65536: 0 follows 65535."
  (should (equal (substring (tetris-mit-display-source-encode-pal16 "\0" 65535) 0 2)
                 (unibyte-string 255 255)))
  (should (equal (substring (tetris-mit-display-source-encode-pal16 "\0" 65536) 0 2)
                 (unibyte-string 0 0)))
  (should (equal (substring (tetris-mit-display-source-encode-pal16 "\0" 258) 0 2)
                 (unibyte-string 1 2)))
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
    (let ((tetris-mit-display-source-sequence t)
          (rows (tetris-mit-test--rows [255 170 0])))
      (setf (tetris-mit-display-source-seq source) #xfffe)
      (dotimes (_ 3)
        (setf (tetris-mit-display-source-last-sent source) 0.0)
        (tetris-mit-display-source-offer source rows)))
    (let ((frames (tetris-mit-dsrc-test--binaries (funcall log))))
      (should (equal (mapcar (lambda (b)
                               (plist-get (tetris-mit-display-source-decode "pal16" b 9 17)
                                          :seq))
                             frames)
                     '(65534 65535 0)))
      (should (cl-every (lambda (b) (= (length b) 155)) frames))
      (should (cl-every (lambda (b)
                          (equal (plist-get (tetris-mit-display-source-decode "pal16" b 9 17)
                                            :cells)
                                 (make-string 153 6)))
                        frames)))))

(ert-deftest tetris-mit-dsrc-test-hex-boundaries-and-equivalence ()
  "hex: h lines of w digits and LF, then an optional blank line.
The pal16 and hex encodings of the same cells decode to the same cells."
  (random "tetris-mit-dsrc")
  (let* ((w 9) (h 17) (n 153) (m (* h (1+ w))))
    (dotimes (_ 20)
      (let* ((cells (tetris-mit-dsrc-test--bytes (cl-loop repeat n collect (random 16))))
             (seq (random 65536))
             (pal16 (tetris-mit-display-source-decode
                     "pal16" (tetris-mit-display-source-encode-pal16 cells seq) w h))
             (hex (tetris-mit-display-source-decode
                   "hex" (tetris-mit-display-source-encode-hex cells w h) w h))
             (blank (tetris-mit-display-source-decode
                     "hex" (tetris-mit-display-source-encode-hex cells w h t) w h)))
        (should (equal (plist-get pal16 :cells) cells))
        (should (equal (plist-get pal16 :seq) seq))
        (should (equal (plist-get hex :cells) cells))
        (should (equal (plist-get blank :cells) cells))))
    (let ((text (tetris-mit-display-source-encode-hex (make-string n 15) w h)))
      (should (= (length text) m))
      (should (string-match-p "\\`\\(fffffffff\n\\)\\{17\\}\\'" text))
      (should-not (eq (aref text 0) ?{))
      (should (equal (tetris-mit-display-source-decode "hex" (upcase text) w h)
                     (list :cells (make-string n 15) :seq nil)))
      (dolist (bad (list "" (substring text 0 -1) (concat text "\n\n")
                         (replace-regexp-in-string "\n" "\r\n" text)))
        (should (equal (tetris-mit-display-source-decode "hex" bad w h)
                       '(:error "bad-frame-length"))))
      (should (equal (tetris-mit-display-source-decode "hex" (concat "g" (substring text 1)) w h)
                     '(:error "bad-format")))
      (should (equal (tetris-mit-display-source-decode "hex" (concat text "x") w h)
                     '(:error "bad-format"))))))

;;;; The lease and the frames sent

(ert-deftest tetris-mit-dsrc-test-granted-frames ()
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (let ((rows (tetris-mit-test--rows [255 170 0])))
      (tetris-mit-display-source-offer source rows)   ; before the grant: dropped
      (should (= (tetris-mit-display-source-dropped source) 1))
      (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
      (should (eq (tetris-mit-display-source-state source) 'granted))
      (should (equal (list (tetris-mit-display-source-w source)
                           (tetris-mit-display-source-h source)
                           (tetris-mit-display-source-format source))
                     '(9 17 "pal16")))
      (should (equal (tetris-mit-display-source-palette source) tetris-mit-dsrc-test--cga))
      (tetris-mit-display-source-offer source rows)
      (let ((frames (tetris-mit-dsrc-test--binaries (funcall log))))
        (should (equal frames (list (make-string 153 6))))
        (should-not (multibyte-string-p (car frames)))))))

(ert-deftest tetris-mit-dsrc-test-hex-frames-are-text ()
  (tetris-mit-dsrc-test--with-source (source log "green-building" :format "hex")
    (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17 30 "hex"))
    (let ((rows (tetris-mit-test--rows)))
      (tetris-mit-test--set rows 0 0 [0 255 255])   ; I: index 11, digit b
      (tetris-mit-display-source-offer source rows))
    (should-not (tetris-mit-dsrc-test--binaries (funcall log)))
    (let ((frame (cadr (car (last (funcall log))))))
      (should (string-prefix-p "b00000000\n000000000\n" frame))
      (should (= (length frame) 170)))))

(ert-deftest tetris-mit-dsrc-test-granted-palette-is-used ()
  "Quantization uses the palette in `granted': on mono, lit cells map to index 1."
  (tetris-mit-dsrc-test--with-source (source log "trs80")
    (tetris-mit-display-source-receive
     source (tetris-mit-dsrc-test--granted
             10 12 30 "pal16" (vconcat (cons "#000000" (make-list 15 "#FFFFFF")))))
    (let ((rows (tetris-mit-test--rows [255 170 0])))
      (tetris-mit-test--set rows 5 4 [0 0 0])
      (tetris-mit-display-source-offer source rows))
    (let ((frame (car (tetris-mit-dsrc-test--binaries (funcall log)))))
      ;; rows 2..13 of 17 are kept, 9 of 10 columns are lit, one is black
      (should (= (length frame) 120))
      (should (= (cl-count 1 frame) 107))
      (should (= (cl-count 0 frame) 13)))))

(ert-deftest tetris-mit-dsrc-test-fps-drops-never-queues ()
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17 10))
    (let ((a (tetris-mit-test--rows [255 0 0]))
          (b (tetris-mit-test--rows [0 255 0]))
          (c (tetris-mit-test--rows [0 0 255])))
      (tetris-mit-display-source-offer source a)     ; sent now
      (tetris-mit-display-source-offer source b)     ; waits for the next slot
      (tetris-mit-display-source-offer source c)     ; replaces b
      (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 1))
      (should (= (tetris-mit-display-source-dropped source) 1))
      (should (tetris-mit--wait-until
               (lambda () (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 2))
               2))
      (should (equal (tetris-mit-dsrc-test--binaries (funcall log))
                     (list (make-string 153 4) (make-string 153 1))))
      (should-not (tetris-mit-display-source-pending source))
      (sit-for 0.3)
      (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 2)))))

(ert-deftest tetris-mit-dsrc-test-busy-not-holder-and-other-errors ()
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (tetris-mit-display-source-receive
     source "{\"op\":\"busy\",\"holder\":\"someone@else\",\"expires\":1789000000}")
    (should (eq (tetris-mit-display-source-state source) 'busy))
    (should (equal (tetris-mit-display-source-holder source) "someone@else"))
    (tetris-mit-display-source-offer source (tetris-mit-test--rows))
    (should-not (tetris-mit-dsrc-test--binaries (funcall log))))
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
    ;; rate, bad-frame-length, bad-format and unknown-op keep the lease
    (dolist (reason '("rate" "bad-frame-length" "bad-format" "unknown-op"))
      (tetris-mit-display-source-receive
       source (format "{\"op\":\"error\",\"reason\":\"%s\"}" reason))
      (should (equal (list reason (tetris-mit-display-source-state source))
                     (list reason 'granted))))
    (should (= (tetris-mit-display-source-rejected source) 4))
    (should (equal (tetris-mit-display-source-last-error source) "unknown-op"))
    (tetris-mit-display-source-receive source "{\"op\":\"error\",\"reason\":\"not-holder\"}")
    (should (eq (tetris-mit-display-source-state source) 'lost))
    (tetris-mit-display-source-offer source (tetris-mit-test--rows))
    (should-not (tetris-mit-dsrc-test--binaries (funcall log)))
    (tetris-mit-display-source-receive source "not json at all")
    (should (eq (tetris-mit-display-source-state source) 'lost))))

(ert-deftest tetris-mit-dsrc-test-renew-when-idle-and-release ()
  (let ((tetris-mit-display-source-renew-interval 0.1))
    (tetris-mit-dsrc-test--with-source (source log "green-building")
      (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
      (should (tetris-mit--wait-until
               (lambda () (member '((op . "renew")) (tetris-mit-dsrc-test--texts (funcall log))))
               3))
      (tetris-mit-display-source-stop source)
      (should (eq (tetris-mit-display-source-state source) 'released))
      (let ((log (funcall log)))
        (should (equal (car (last (tetris-mit-dsrc-test--texts log))) '((op . "release"))))
        (should (equal (car (last log)) '(close))))
      (should-not (memq source tetris-mit-display-source--sources)))))

(ert-deftest tetris-mit-dsrc-test-stock-tetris-gamegrid ()
  "Stock tetris.el at 10 x 12 (trs80): the playing field goes out as palette indices."
  (let ((fake (tetris-mit-dsrc-test--fake))
        (buffer (generate-new-buffer "*tetris-dsrc-test*")))
    (unwind-protect
        (with-current-buffer buffer
          (tetris-mode)
          (setq-local tetris-width 10)
          (setq-local tetris-height 12)
          (tetris-start-game)
          (gamegrid-kill-timer)
          (let ((source (tetris-mit-display-source-tetris "trs80" nil (car fake))))
            (should (equal (alist-get 'display (car (tetris-mit-dsrc-test--texts
                                                      (funcall (cdr fake)))))
                           "trs80"))
            (let ((rows (tetris-mit-display-source-gamegrid-rows)))
              (should (= (length rows) 12))
              (should (= (length (aref rows 0)) 10))
              (should (= 4 (cl-loop for row across rows
                                    sum (cl-count-if-not (lambda (c) (equal c [0 0 0])) row)))))
            (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 10 12))
            (let ((frames (tetris-mit-dsrc-test--binaries (funcall (cdr fake))))
                  (index (tetris-mit-display-source-quantize
                          (tetris-mit-display-source--cell-rgb tetris-shape)
                          tetris-mit-dsrc-test--cga)))
              (should (= (length frames) 1))  ; the field is sent on the grant
              (should (= (length (car frames)) 120))
              (should (equal (car frames)
                             (tetris-mit-display-source-indices
                              (tetris-mit-display-source-gamegrid-rows) 10 12
                              tetris-mit-dsrc-test--cga)))
              (should (= 4 (cl-count index (car frames)))))
            ;; a move changes cells; the advice offers the new field (paced)
            (tetris-move-left)
            (should (tetris-mit--wait-until
                     (lambda () (>= (length (tetris-mit-dsrc-test--binaries
                                             (funcall (cdr fake))))
                                    2))
                     2))
            (should (equal (car (last (tetris-mit-dsrc-test--binaries (funcall (cdr fake)))))
                           (tetris-mit-display-source-indices
                            (tetris-mit-display-source-gamegrid-rows) 10 12
                            tetris-mit-dsrc-test--cga)))))
      (kill-buffer buffer))
    ;; kill-buffer released the lease
    (should (equal (car (last (tetris-mit-dsrc-test--texts (funcall (cdr fake)))))
                   '((op . "release"))))
    (should-not (advice-member-p #'tetris-mit-display-source--after-set-cell
                                 'gamegrid-set-cell))))

(ert-deftest tetris-mit-dsrc-test-game-frames-from-the-overlay-display ()
  (let ((fake (tetris-mit-dsrc-test--fake))
        (buffer (tetris-mit-display-buffer)))
    (unwind-protect
        (let ((source (tetris-mit-display-source-game "green-building" nil (car fake)))
              (rows (tetris-mit-test--rows [0 255 255])))
          (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
          (setf (tetris-mit-display-source-last-sent source) 0.0)
          (tetris-mit-display-show rows 5 nil buffer)
          (should (equal (car (last (tetris-mit-dsrc-test--binaries (funcall (cdr fake)))))
                         (make-string 153 11)))
          (tetris-mit-display-source-stop source)
          (should-not (memq #'tetris-mit-display-source--game-frame
                            tetris-mit-display-frame-functions)))
      (kill-buffer buffer))))

(ert-deftest tetris-mit-dsrc-test-refuses-non-loopback-relays ()
  "Never reach a live relay by accident, such as wss://wal.sh."
  (cl-letf (((symbol-function 'websocket-open)
             (lambda (&rest _) (error "Must not connect"))))
    (should-error (tetris-mit-display-source-open
                   "green-building" :url "wss://wal.sh/tools/display/ws")
                  :type 'user-error)
    (should-error (tetris-mit-display-source--check-url "http://127.0.0.1/x")
                  :type 'user-error))
  (should-not (tetris-mit-display-source--check-url tetris-mit-display-source-url))
  (should (string-prefix-p "ws://127.0.0.1:" tetris-mit-display-source-url))
  (unless (locate-library "websocket")
    (should-error (tetris-mit-display-source--websocket-link
                   "ws://127.0.0.1:1/tools/display/ws" nil)
                  :type 'user-error)))

;;;; The displays kit's fixtures, when present

(defun tetris-mit-dsrc-test--fixtures ()
  "The fixture directory of contrib/displays/contract, or skip the test."
  (let ((dir (or (getenv "TETRIS_MIT_DISPLAY_FIXTURES")
                 (expand-file-name "contrib/displays/contract/fixtures/" tetris-mit-root))))
    (if (file-directory-p dir)
        (file-name-as-directory dir)
      (ert-skip (format "no display fixtures at %s" dir)))))

(defun tetris-mit-dsrc-test--fixture (name)
  "The fixture file NAME.json, parsed."
  (tetris-mit-read-trace (expand-file-name (concat name ".json")
                                           (tetris-mit-dsrc-test--fixtures))))

(defun tetris-mit-dsrc-test--case-data (case)
  "The frame of a frames.json CASE: bytes as a unibyte string, text as a string."
  (let ((bytes (alist-get 'bytes case))
        (bytes-rle (alist-get 'bytes_rle case))
        (text-rle (alist-get 'text_rle case)))
    (cond (bytes (tetris-mit-dsrc-test--bytes bytes))
          (bytes-rle (tetris-mit-dsrc-test--bytes
                      (cl-loop for pair across bytes-rle
                               append (make-list (aref pair 1) (aref pair 0)))))
          (text-rle (mapconcat (lambda (pair) (apply #'concat (make-list (aref pair 1)
                                                                         (aref pair 0))))
                               text-rle ""))
          (t (or (alist-get 'text case) "")))))

(defun tetris-mit-dsrc-test--palette16 (colours)
  "The spec's level rule: entry i of 16 is COLOURS[level(i)]."
  (let ((n (length colours)))
    (vconcat (cl-loop for i below 16
                      collect (elt colours (if (zerop i) 0
                                             (max 1 (/ (+ (* 2 i (1- n)) 15) 30))))))))

(ert-deftest tetris-mit-dsrc-test-fixture-frames ()
  "frames.json: every pal16 and hex case decodes to its expected result."
  (let ((n 0))
    (cl-loop
     for case across (alist-get 'cases (tetris-mit-dsrc-test--fixture "frames"))
     for format = (alist-get 'format case)
     when (member format tetris-mit-display-source-formats)
     do (let* ((expect (alist-get 'expect case))
               (want (if (eq (alist-get 'ok expect) t)
                         (list :cells (tetris-mit-dsrc-test--bytes
                                       (or (alist-get 'cells expect)
                                           (cl-loop for pair across (alist-get 'cells_rle expect)
                                                    append (make-list (aref pair 1)
                                                                      (aref pair 0)))))
                               :seq (alist-get 'seq expect))
                       (list :error (alist-get 'reason expect)))))
          (setq n (1+ n))
          (should (equal (list (alist-get 'name case)
                               (tetris-mit-display-source-decode
                                format (tetris-mit-dsrc-test--case-data case)
                                (alist-get 'w case) (alist-get 'h case)))
                         (list (alist-get 'name case) want)))))
    (should (> n 100))))

(ert-deftest tetris-mit-dsrc-test-fixture-equivalence ()
  "equivalence.json: pal16 and hex decode to the same cells.
The source's encoders write both forms byte for byte."
  (cl-loop
   for case across (alist-get 'cases (tetris-mit-dsrc-test--fixture "equivalence"))
   do (let* ((w (alist-get 'w case)) (h (alist-get 'h case))
             (name (alist-get 'name case))
             (bytes (tetris-mit-dsrc-test--bytes
                     (alist-get 'bytes (alist-get 'pal16 case))))
             (text (alist-get 'text (alist-get 'hex case)))
             (p (tetris-mit-display-source-decode "pal16" bytes w h))
             (x (tetris-mit-display-source-decode "hex" text w h)))
        (should (plist-get p :cells))
        (should (equal (list name (plist-get p :cells)) (list name (plist-get x :cells))))
        (should (equal (tetris-mit-display-source-encode-pal16 (plist-get p :cells)
                                                              (plist-get p :seq))
                       bytes))
        (should (equal (tetris-mit-display-source-encode-hex
                        (plist-get x :cells) w h (= (length text) (1+ (* h (1+ w)))))
                       text)))))

(ert-deftest tetris-mit-dsrc-test-fixture-quantize ()
  "quantize.json: the nearest entry, with ties to the lower index, on every named palette."
  (let* ((caps (tetris-mit-read-trace
                (expand-file-name "../wal-sh-display-0.2.1/capabilities.json"
                                  (tetris-mit-dsrc-test--fixtures))))
         (palettes (alist-get 'palettes caps))
         (n 0))
    (cl-loop
     for case across (alist-get 'cases (tetris-mit-dsrc-test--fixture "quantize"))
     do (let* ((name (alist-get 'palette case))
               (colours (alist-get (intern name) palettes))
               (pal16 (tetris-mit-display-source-palette-rgb
                       (tetris-mit-dsrc-test--palette16 colours)))
               (rgb (alist-get 'rgb case)))
          (setq n (1+ n))
          (should (equal (list name rgb (tetris-mit-display-source-quantize rgb pal16))
                         (list name rgb (alist-get 'index case))))))
    (should (> n 100))))

;;;; Against the displays kit's mock relay, when present

(defconst tetris-mit-dsrc-test--dir
  (file-name-directory (or load-file-name buffer-file-name default-directory))
  "The directory of this test file, which holds display-relay-bridge.py.")

(defvar tetris-mit-dsrc-test--viewer nil
  "What the bridge's viewer received, newest first.
Each item is (text STRING) or (binary BYTES).")

(defvar tetris-mit-dsrc-test--bridged nil
  "The source session that the bridge delivers the relay's messages to.")

(defun tetris-mit-dsrc-test--relay-dir ()
  "The contrib/displays directory of a v0.2.1 mock relay, or skip the test."
  (let* ((dir (or (getenv "TETRIS_MIT_DISPLAYS_DIR")
                  (expand-file-name "contrib/displays" tetris-mit-root)))
         (relay (expand-file-name "demo/relay.py" dir)))
    (unless (and (file-readable-p relay)
                 (with-temp-buffer
                   (insert-file-contents relay)
                   (search-forward "spec v0.2.1" nil t)))
      (ert-skip (format "no v0.2.1 mock relay under %s" dir)))
    (file-name-as-directory dir)))

(defun tetris-mit-dsrc-test--hex (bytes)
  "BYTES, a unibyte string, as lower-case hex."
  (mapconcat (lambda (b) (format "%02x" b)) bytes ""))

(defun tetris-mit-dsrc-test--unhex (hex)
  "HEX as a unibyte string."
  (let ((out (make-string (/ (length hex) 2) 0)))
    (dotimes (i (length out))
      (aset out i (string-to-number (substring hex (* 2 i) (+ 2 (* 2 i))) 16)))
    out))

(defun tetris-mit-dsrc-test--bridge-filter (proc chunk)
  "Deliver the bridge PROC's lines in CHUNK: to the source, or to the viewer log."
  (let ((data (concat (process-get proc 'pending) chunk)) newline)
    (while (setq newline (string-search "\n" data))
      (let ((line (substring data 0 newline)))
        (setq data (substring data (1+ newline)))
        (pcase (split-string line " ")
          (`("S" "T" ,hex)
           (when tetris-mit-dsrc-test--bridged
             (tetris-mit-display-source-receive
              tetris-mit-dsrc-test--bridged
              (decode-coding-string (tetris-mit-dsrc-test--unhex hex) 'utf-8))))
          (`("V" "T" ,hex)
           (push (list 'text (decode-coding-string (tetris-mit-dsrc-test--unhex hex) 'utf-8))
                 tetris-mit-dsrc-test--viewer))
          (`("V" "B" ,hex)
           (push (list 'binary (tetris-mit-dsrc-test--unhex hex))
                 tetris-mit-dsrc-test--viewer)))))
    (process-put proc 'pending data)))

(defun tetris-mit-dsrc-test--bridge-link (bridge)
  "A link that sends through the process BRIDGE, on a fresh source connection."
  (process-send-string bridge "O\n")
  (tetris-mit-display-source-make-link
   :send-text (lambda (text)
                (process-send-string
                 bridge (concat "T " (tetris-mit-dsrc-test--hex
                                      (encode-coding-string text 'utf-8))
                                "\n")))
   :send-binary (lambda (bytes)
                  (process-send-string
                   bridge (concat "B " (tetris-mit-dsrc-test--hex bytes) "\n")))
   :close (lambda () (process-send-string bridge "C\n"))))

(defun tetris-mit-dsrc-test--viewer-texts (op)
  "The viewer's control messages with OP, oldest first."
  (cl-loop for item in (reverse tetris-mit-dsrc-test--viewer)
           for msg = (and (eq (car item) 'text) (string-prefix-p "{" (cadr item))
                          (tetris-mit--json-parse (cadr item)))
           when (and msg (equal (alist-get 'op msg) op)) collect msg))

(defun tetris-mit-dsrc-test--viewer-frames ()
  "The frames the viewer received, oldest first."
  (cl-loop for item in (reverse tetris-mit-dsrc-test--viewer)
           when (eq (car item) 'binary) collect (cadr item)))

(defun tetris-mit-dsrc-test--spec-rows ()
  "A 17 x 9 frame in all ten SPEC colours, in diagonal stripes."
  (let ((rows (tetris-mit-test--rows))
        (codes '("." "W" "I" "J" "L" "O" "S" "Z" "T" "G")))
    (dotimes (r tetris-mit-rows)
      (dotimes (c tetris-mit-cols)
        (tetris-mit-test--set rows r c (apply #'vector (tetris-mit-palette-rgb
                                                        (nth (% (+ r c) 10) codes))))))
    rows))

(ert-deftest tetris-mit-dsrc-test-mock-relay ()
  "The source against the displays kit's mock relay (spec v0.2.1), over a bridge.
It covers `reserve' and `granted' (the green-building grid and the cga
palette), and pal16 frames with the sequences 0, 65534 and 65535, which
a viewer sees as the right indices.  A 0 after 65535 is dropped with
`rate' (the relay's literal rule).  A 152-byte frame and a byte of 16
are refused while the lease is kept.  Then release, and a hex session
that the relay fans out as pal16."
  (let* ((dir (tetris-mit-dsrc-test--relay-dir))
         (python (or (tetris-mit-test--python) (ert-skip "no Python for the relay")))
         (process-environment (cons "PYTHONDONTWRITEBYTECODE=1" process-environment))
         (relay-buffer (generate-new-buffer " *mock-relay*"))
         (relay (let ((default-directory dir))
                  (make-process :name "mock-relay" :buffer relay-buffer :noquery t
                                :connection-type 'pipe
                                :command (list python "-m" "demo" "relay" "--port" "0"))))
         (url (tetris-mit--wait-until
               (lambda ()
                 (with-current-buffer relay-buffer
                   (save-excursion
                     (goto-char (point-min))
                     (and (re-search-forward "mock relay \\(ws://[^ \n]+\\)" nil t)
                          (match-string 1)))))
               30))
         (bridge nil)
         (rows (tetris-mit-dsrc-test--spec-rows))
         (cells (tetris-mit-display-source-indices rows 9 17 tetris-mit-dsrc-test--cga)))
    (setq tetris-mit-dsrc-test--viewer nil
          tetris-mit-dsrc-test--bridged nil)
    (unwind-protect
        (progn
          (should url)
          (setq bridge (make-process
                        :name "relay-bridge" :noquery t :connection-type 'pipe
                        :coding 'binary :filter #'tetris-mit-dsrc-test--bridge-filter
                        :command (list python tetris-mit-display-source-bridge-script
                                       url "green-building")))
          (should (tetris-mit--wait-until
                   (lambda () (tetris-mit-dsrc-test--viewer-texts "caps")) 20))
          (let ((caps (car (tetris-mit-dsrc-test--viewer-texts "caps"))))
            (should (equal (mapcar (lambda (k) (alist-get k caps)) '(w h fps format))
                           '(9 17 30 "pal16")))
            (should (equal (tetris-mit-display-source-palette-rgb (alist-get 'palette caps))
                           tetris-mit-dsrc-test--cga)))
          (let* ((tetris-mit-display-source-sequence t)
                 (source (setq tetris-mit-dsrc-test--bridged
                               (tetris-mit-display-source-open
                                "green-building" :name "emacs@ert"
                                :link (tetris-mit-dsrc-test--bridge-link bridge))))
                 (send-binary (tetris-mit-display-source-link-send-binary
                               (tetris-mit-display-source-link source))))
            (should (tetris-mit--wait-until
                     (lambda () (eq (tetris-mit-display-source-state source) 'granted)) 10))
            (should (equal (list (tetris-mit-display-source-w source)
                                 (tetris-mit-display-source-h source)
                                 (tetris-mit-display-source-fps source)
                                 (tetris-mit-display-source-format source))
                           '(9 17 30 "pal16")))
            (should (equal (tetris-mit-display-source-palette source) tetris-mit-dsrc-test--cga))
            (dolist (seq '(0 65534 65535 0))
              (setf (tetris-mit-display-source-seq source) seq)
              (sleep-for 0.1)
              (tetris-mit-display-source-offer source rows))
            (should (tetris-mit--wait-until
                     (lambda () (equal (tetris-mit-display-source-last-error source) "rate"))
                     5))
            (should (equal (tetris-mit-dsrc-test--viewer-frames) (list cells cells cells)))
            (sleep-for 0.1)
            (funcall send-binary (make-string 152 0))
            (should (tetris-mit--wait-until
                     (lambda () (equal (tetris-mit-display-source-last-error source)
                                       "bad-frame-length"))
                     5))
            (let ((bad (copy-sequence cells)))
              (aset bad 0 16)
              (funcall send-binary bad))
            (should (tetris-mit--wait-until
                     (lambda () (equal (tetris-mit-display-source-last-error source)
                                       "bad-format"))
                     5))
            (should (= (tetris-mit-display-source-rejected source) 3))
            (should (eq (tetris-mit-display-source-state source) 'granted))
            (should (= (length (tetris-mit-dsrc-test--viewer-frames)) 3))
            (tetris-mit-display-source-stop source)
            (should (tetris-mit--wait-until
                     (lambda ()
                       (let ((leases (tetris-mit-dsrc-test--viewer-texts "lease")))
                         (and (cl-some (lambda (m) (equal (alist-get 'holder m) "emacs@ert"))
                                       leases)
                              (null (alist-get 'holder (car (last leases)))))))
                     5)))
          (let ((source (setq tetris-mit-dsrc-test--bridged
                              (tetris-mit-display-source-open
                               "green-building" :name "emacs@ert" :format "hex"
                               :link (tetris-mit-dsrc-test--bridge-link bridge)))))
            (should (tetris-mit--wait-until
                     (lambda () (eq (tetris-mit-display-source-state source) 'granted)) 10))
            (should (equal (tetris-mit-display-source-format source) "hex"))
            (sleep-for 0.1)
            (tetris-mit-display-source-offer source rows)
            (should (tetris-mit--wait-until
                     (lambda () (= (length (tetris-mit-dsrc-test--viewer-frames)) 4)) 5))
            (should (equal (car (last (tetris-mit-dsrc-test--viewer-frames))) cells))
            (should (= (tetris-mit-display-source-rejected source) 0))
            (tetris-mit-display-source-stop source)))
      (setq tetris-mit-dsrc-test--bridged nil)
      (when (and bridge (process-live-p bridge)) (delete-process bridge))
      (when (process-live-p relay) (delete-process relay))
      (kill-buffer relay-buffer))))

(provide 'tetris-mit-display-source-test)

;;; tetris-mit-display-source-test.el ends here
