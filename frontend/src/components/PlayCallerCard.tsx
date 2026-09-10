// Play-caller attribution, framed as what it actually is.
//
// `verified` is false on every row shipped so far: the coordinator mapping is
// model-generated and has not been through human review. So this never says
// "Todd Monken called 47.9% passes" — it says this is Baltimore's offence,
// with Monken named as the presumed caller and the claim marked unverified.
//
// Two further qualifiers are surfaced when present:
//  - fell_back_to_head_coach: the coordinator was unknown, so the head coach
//    was substituted; the name is a stand-in, not a finding.
//  - note: known nflverse gaps. For 2024+ nflverse records one coach per
//    team-season and misses in-season firings, so e.g. NYJ 2024 attributes
//    all 17 games to Robert Saleh though Jeff Ulbrich coached 12 of them.

import type { PlayCaller } from '../api';

// Most team names are plural ("Ravens", "49ers"), so the possessive is a bare
// apostrophe; a few are not ("Miami Dolphins" is, "Washington Commanders" is,
// but the field can also hold a singular label).
const poss = (name: string) => (name.endsWith('s') ? `${name}’` : `${name}’s`);

export function PlayCallerCard({ caller, teamName, games }: {
  caller: PlayCaller | null; teamName: string; games: number;
}) {
  if (!caller) {
    return (
      <div className="verdict warn" style={{ margin: '0 0 16px' }}>
        <strong>Play-caller unknown</strong>
        <div className="note" style={{ marginTop: 4 }}>
          No coaching record for this team-season. Everything below is {teamName}’s
          offence with no attribution attached.
        </div>
      </div>
    );
  }
  return (
    <div className={`verdict ${caller.verified ? '' : 'warn'}`} style={{ margin: '0 0 16px' }}>
      <strong>
        {poss(teamName)} offence{games ? ` over ${games} games` : ''} — presumed play-caller{' '}
        {caller.name}
        {!caller.verified && <span className="badge unverified">unverified</span>}
        {caller.is_head_coach && <span className="badge info">head coach</span>}
      </strong>
      <div className="note" style={{ marginTop: 4 }}>
        {caller.verified
          ? 'Attribution confirmed against the coaching record.'
          : 'Attribution is model-generated and has not been reviewed, so these are the team’s ' +
            'play-calling tendencies with ' + caller.name + ' named as the presumed caller — ' +
            'not a measured record of what one coach called.'}
      </div>
      {caller.fell_back_to_head_coach && (
        <div className="note" style={{ marginTop: 4, color: 'var(--bad)' }}>
          The offensive coordinator was unknown for this team-season, so the head coach
          was substituted. The name is a stand-in.
        </div>
      )}
      {caller.note && (
        <div className="caller-note">
          <span className="badge info">data limit</span> {caller.note}
        </div>
      )}
    </div>
  );
}
