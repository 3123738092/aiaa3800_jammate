// Group changed notes on their original clock. A merged note overlaps each
// of its original fragments, while adjacent independent edits stay separate.
export function noteDifferences(before,after) {
  const key = n => [n.pitchMidi,n.startSec,n.durationSec,n.velocity01].join('/');
  const keysBefore = new Set(before.map(key)), keysAfter = new Set(after.map(key));
  const changes = [
    ...before.filter(n => !keysAfter.has(key(n))).map(note => ({side:'before',note})),
    ...after.filter(n => !keysBefore.has(key(n))).map(note => ({side:'after',note})),
  ].sort((a,b) => a.note.startSec-b.note.startSec);
  const groups = [];
  for (const {side,note} of changes) {
    let group = groups.at(-1);
    if (!group || note.startSec >= group.end - 1e-6) {
      group = {start:note.startSec,end:note.startSec,before:[],after:[]};
      groups.push(group);
    }
    group[side].push(note.pitchMidi);
    group.end = Math.max(group.end,note.startSec+note.durationSec);
  }
  return groups;
}
