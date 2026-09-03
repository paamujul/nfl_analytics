// Season/phase selection shared across pages (defaults to the live 2026 preseason).
import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { api } from './api';
import type { SeasonPhases } from './api';

interface SeasonState {
  season: number;
  phase: string;
  available: SeasonPhases[];
  setSeason: (s: number) => void;
  setPhase: (p: string) => void;
}

const Ctx = createContext<SeasonState>({
  season: 2026, phase: 'pre', available: [],
  setSeason: () => {}, setPhase: () => {},
});

export const PHASE_LABEL: Record<string, string> = {
  pre: 'Preseason', reg: 'Regular season', post: 'Postseason',
};

export function SeasonProvider({ children }: { children: ReactNode }) {
  const saved = (() => {
    try { return JSON.parse(localStorage.getItem('nfl-season') ?? 'null'); } catch { return null; }
  })();
  const [season, setSeason] = useState<number>(saved?.season ?? 2026);
  const [phase, setPhase] = useState<string>(saved?.phase ?? 'pre');
  const [available, setAvailable] = useState<SeasonPhases[]>([]);

  useEffect(() => {
    localStorage.setItem('nfl-season', JSON.stringify({ season, phase }));
  }, [season, phase]);

  useEffect(() => {
    api.seasons().then((rows) => {
      setAvailable(rows);
      if (saved) return; // respect the user's previous choice
      // default to the newest season/phase that actually has played games
      const played = rows.flatMap((r) =>
        r.phases.filter((p) => p.played > 0).map((p) => ({ season: r.season, phase: p.phase })));
      if (played.length) {
        const last = played[played.length - 1];
        setSeason(last.season);
        setPhase(last.phase);
      }
      // deliberate: App.tsx falls back to a hardcoded season/phase list, so a
      // failed lookup degrades to sane defaults rather than blocking the page
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pick = (s: number) => {
    setSeason(s);
    const row = available.find((r) => r.season === s);
    if (row && !row.phases.some((p) => p.phase === phase && p.played > 0)) {
      const withGames = row.phases.filter((p) => p.played > 0);
      if (withGames.length) setPhase(withGames[withGames.length - 1].phase);
    }
  };

  return (
    <Ctx.Provider value={{ season, phase, available, setSeason: pick, setPhase }}>
      {children}
    </Ctx.Provider>
  );
}

export const useSeason = () => useContext(Ctx);
