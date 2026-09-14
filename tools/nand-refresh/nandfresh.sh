#!/bin/sh
# nandfresh.sh - rewrite every file of a NAND rootfs so its retention clock restarts.
#
# Slice: kb/research/nand-retention  (READ IT FIRST - especially the order of
# operations: an md5 sweep and a restore of every mismatching file come BEFORE this
# script, or the refresh bakes silently mis-corrected bytes in for good.)
#
# WHY: SLC NAND of the 2010s loses charge in pages that were written once at the
# factory and never rewritten. yaffs2 has no background scrub: a chunk whose ECC can
# no longer be fixed is returned to the caller AS IS, so cold files rot silently.
# Reading a file and writing it back makes a log-structured filesystem allocate
# fresh chunks with fresh ECC, which restarts the retention clock - and the
# read-disturb clock with it. Pure POSIX shell, coreutils only (cp/cmp/mv/find/sync).
#
# WHAT IT DOES, per regular file under ROOTS (same device as /, symlinks untouched):
#   cp -p f f.new  ->  cmp f f.new  ->  mv f.new f   (the rename is atomic; a running
#   process keeps its old inode, so the application may stay up)  ->  sync every
#   SYNC_EVERY files. Hard-link groups: atomic mv onto the first name, then `ln -f`
#   every other name of the old inode onto the fresh file (find -samefile). NEVER an
#   in-place overwrite - a truncate+write is not atomic and a concurrent reader sees
#   a torn file.
#   Run it with the application that owns the rootfs STOPPED: yaffs2 has one writer
#   lock, so the pass is 5-8x faster and there is no second writer. Reboot afterwards.
# SAFETY: refuses when the rootfs is fuller than MAX_USE % or free space is below the
#   largest file + a margin; never descends into EXCLUDE (state directories, /tmp,
#   /mnt, /proc, /sys, /dev, /var/log). Every step is logged. A cmp mismatch - the
#   file reads differently twice, i.e. an unstable page - is logged as UNSTABLE and
#   the file is LEFT ALONE: that one is a restore job, not a refresh job.
# AFTER THE PASS: directory object headers are rewritten (touch -c -r), then a
#   throwaway file fills the free space and is deleted, so garbage collection erases
#   the now-dirty old blocks immediately instead of whenever something needs them.
# ORDER OF OPERATIONS FOR A UNIT:
#   (1) md5 sweep against the rootfs's own package manifests or a healthy sibling of
#       the same generation, and restore every mismatch - 1-bit hardware ECC
#       MIS-CORRECTS a 3-bit flip SILENTLY, so the uncorrectable counters understate
#       the rot and a refresh alone would make the bad bytes permanent;
#   (2) this script;
#   (3) the raw kernel / bootloader partitions via nanddump -> verify -> flash_erase
#       -> nandwrite (a separate procedure; keep the dump, it is your undo).
#   Cadence: every 2-3 years, at once on any eccFixed/eccUnfixed increase.
#
# Usage: sh nandfresh.sh [--dry] [--roots "/bin /sbin ..."] [--out FILE]
#   --dry reads every file TWICE and compares digests, which finds unstable pages
#   without writing anything at all. Start there.
#   env: MAX_USE (default 90), MARGIN_KB (default 8192), SYNC_EVERY (default 100),
#        STATE_DIR (default /var/tmp), EXCLUDE (default below), UNIT (report label)

DRY=0
ROOTS="/bin /sbin /lib /etc /usr /root /opt /var/lib /var/www /home"
OUT=""
MAX_USE=${MAX_USE:-90}
MARGIN_KB=${MARGIN_KB:-8192}
SYNC_EVERY=${SYNC_EVERY:-100}
STATE_DIR=${STATE_DIR:-/var/tmp}
# Directories never touched: live application state and databases, volatile
# filesystems, logs. Add your own - anything with a writer other than this script.
EXCLUDE=${EXCLUDE:-"/tmp /mnt /media /proc /sys /dev /run /var/log /var/tmp"}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry) DRY=1 ;;
    --roots) shift; ROOTS="$1" ;;
    --out) shift; OUT="$1" ;;
    --exclude) shift; EXCLUDE="$1" ;;
    -h|--help) sed -n '2,46p' "$0"; exit 0 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
  shift
done

UNIT=${UNIT:-$(uname -n)}
UNIT=$(echo "$UNIT" | tr -cd 'A-Za-z0-9._-')
[ -n "$UNIT" ] || UNIT=unknown
TS=$(date +%Y%m%d_%H%M%S)
if [ -z "$OUT" ]; then
  mkdir -p "$STATE_DIR" 2>/dev/null
  OUT="$STATE_DIR/nandfresh_${UNIT}_${TS}.log"
fi

# ATOMIC lock. mkdir is the only atomic create-or-fail primitive a POSIX shell has.
# Two instances racing on the same file corrupted two hard-link groups on a unit
# where a test-then-create lock let both in.
LOCK=/tmp/nandfresh.lock.d
if ! mkdir "$LOCK" 2>/dev/null; then echo "already running ($LOCK)" >&2; exit 3; fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM
if ps -eo pid,cmd 2>/dev/null | grep -v "^ *$$ " | grep -q '[n]andfresh.sh'; then
  echo "another nandfresh.sh process is alive - refusing to start" >&2; exit 3
fi

log() { echo "$(date +%H:%M:%S) $*" >> "$OUT"; }
fs_stats() {
  grep -i 'eccFixed\|eccUnfixed\|nBlockErasures\|nErasedBlocks\|nPageWrites\|nPageReads' \
       /proc/yaffs 2>/dev/null | tr -s ' .' ' ' | tr '\n' ';'
}
# Build the find(1) prune expression from EXCLUDE.
prune=""
for d in $EXCLUDE; do prune="$prune -path $d -prune -o"; done

log "nandfresh start unit=$UNIT dry=$DRY roots=[$ROOTS] kernel=$(uname -r)"
log "counters before: $(fs_stats)"

# ---- preflight -----------------------------------------------------------------
DFL=$(df -k / | tail -1)
USE=$(echo "$DFL" | awk '{gsub("%","",$5); print $5}')
FREE_KB=$(echo "$DFL" | awk '{print $4}')
if [ "$USE" -gt "$MAX_USE" ]; then log "ABORT rootfs ${USE}% used > ${MAX_USE}%"; exit 4; fi
BIGGEST_KB=$(find $ROOTS -xdev $prune -type f -printf '%k\n' 2>/dev/null | sort -n | tail -1)
[ -n "$BIGGEST_KB" ] || BIGGEST_KB=0
NEED_KB=$((BIGGEST_KB + MARGIN_KB))
if [ "$FREE_KB" -lt "$NEED_KB" ]; then
  log "ABORT free ${FREE_KB} KB < biggest ${BIGGEST_KB} KB + margin ${MARGIN_KB} KB"; exit 5
fi
TOTAL=$(find $ROOTS -xdev $prune -type f -print 2>/dev/null | wc -l)
log "preflight ok: use=${USE}% free=${FREE_KB}KB biggest=${BIGGEST_KB}KB files=$TOTAL"

# ---- main loop -----------------------------------------------------------------
n=0; ok=0; unstable=0; failed=0
find $ROOTS -xdev $prune -type f -print 2>/dev/null | while IFS= read -r f; do
  n=$((n+1))
  case "$f" in
    *.nandfresh-new) rm -f "$f"; continue ;;
  esac
  [ -f "$f" ] || continue
  tmp="$f.nandfresh-new"
  if [ "$DRY" = 1 ]; then
    # Read twice and compare digests: finds unstable pages without writing anything.
    # (cmp f f short-circuits on the same inode, so hash instead.)
    a=$(md5sum < "$f" 2>/dev/null); b=$(md5sum < "$f" 2>/dev/null)
    if [ "$a" != "$b" ]; then log "UNSTABLE $f"; unstable=$((unstable+1)); fi
    if [ $((n % SYNC_EVERY)) -eq 0 ]; then log "progress $n/$TOTAL unstable=$unstable"; fi
    continue
  fi
  if ! cp -p "$f" "$tmp" 2>>"$OUT"; then
    log "FAIL cp $f"; rm -f "$tmp"; failed=$((failed+1)); continue
  fi
  if ! cmp -s "$f" "$tmp"; then
    log "UNSTABLE $f (reads differ)"; rm -f "$tmp"; unstable=$((unstable+1)); continue
  fi
  links=$(stat -c %h "$f" 2>/dev/null || echo 1)
  if [ "$links" -gt 1 ]; then
    # Hard-link group. Never rewrite in place: a truncate+write is not atomic and a
    # concurrent reader or copier sees a torn file. Move onto the first name, then
    # re-point every other name of the OLD inode at the fresh file (ln -f is atomic
    # per name).
    others=$(find $ROOTS -xdev $prune -samefile "$f" -print 2>/dev/null | grep -v -x -F "$f")
    if mv -f "$tmp" "$f" 2>>"$OUT"; then
      ok=$((ok+1))
      echo "$others" | while IFS= read -r o; do
        [ -n "$o" ] && ln -f "$f" "$o" 2>>"$OUT"
      done
    else
      log "FAIL mv $f"; rm -f "$tmp"; failed=$((failed+1))
    fi
  else
    if mv -f "$tmp" "$f" 2>>"$OUT"; then ok=$((ok+1));
    else log "FAIL mv $f"; rm -f "$tmp"; failed=$((failed+1)); fi
  fi
  if [ $((n % SYNC_EVERY)) -eq 0 ]; then
    sync; log "progress $n/$TOTAL ok=$ok unstable=$unstable failed=$failed"
  fi
done
sync

if [ "$DRY" != 1 ]; then
  # Directory object headers are never rewritten by file traffic, and they rot like
  # anything else. A setattr with the same timestamp rewrites the header in place.
  find $ROOTS -xdev $prune -type d -print 2>/dev/null | while IFS= read -r d; do
    touch -c -r "$d" "$d" 2>/dev/null
  done
  sync
  # Drive garbage collection so the now-dirty old blocks are erased immediately
  # rather than lingering until something happens to need them: fill most of the
  # free space with one throwaway file, then delete it.
  FREE_KB=$(df -k / | tail -1 | awk '{print $4}')
  FILL_MB=$(( (FREE_KB - MARGIN_KB) / 1024 ))
  if [ "$FILL_MB" -gt 16 ]; then
    log "gc fill ${FILL_MB} MB"
    dd if=/dev/zero of=/root/.nandfresh_gcfill bs=1M count=$FILL_MB 2>/dev/null; sync
    rm -f /root/.nandfresh_gcfill; sync
  fi
fi

# The while loop above ran in a subshell, so its counters are gone - recount from
# the log for the summary.
log "counters after:  $(fs_stats)"
log "df after: $(df -k / | tail -1)"
log "nandfresh done: unstable=$(grep -c ' UNSTABLE ' "$OUT") failed=$(grep -c ' FAIL ' "$OUT") (see progress lines for ok counts)"
echo "report: $OUT"
exit 0
