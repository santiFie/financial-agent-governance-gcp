import React, { useState } from 'react';
import { ChevronDown, ChevronRight, GitBranch } from 'lucide-react';
import { TimelineEvent } from '../types';

interface ExecutionTimelineProps {
  events: TimelineEvent[];
}

const STAGE_COLOR: Record<string, string> = {
  sanitizer: '#06b6d4',
  document_requested: '#38bdf8',
  document_ingested: '#38bdf8',
  plan: '#818cf8',
  replan: '#f43f5e',
  readonly_result: '#10b981',
  tool_call_staged: '#f59e0b',
  action_result: '#f59e0b',
  pep_decision: '#a78bfa',
  hitl_pause: '#f59e0b',
  result: '#10b981',
};

// Read-only view of what the graph did in this turn. It shows; it never blocks or changes anything.
export const ExecutionTimeline: React.FC<ExecutionTimelineProps> = ({ events }) => {
  const [open, setOpen] = useState(false);
  if (!events || events.length === 0) return null;

  const replanned = events.filter((e) => e.added_by_replan).length;

  return (
    <div style={{ marginTop: '10px', fontSize: '0.75rem' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          background: 'transparent', border: 'none', cursor: 'pointer', color: '#a5b4fc',
          display: 'flex', alignItems: 'center', gap: '4px', fontWeight: 600, padding: 0,
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <GitBranch size={13} /> Ver ejecución del grafo ({events.length} eventos
        {replanned > 0 ? `, ${replanned} paso(s) agregado(s) al replanificar` : ''})
      </button>

      {open && (
        <ol style={{ listStyle: 'none', margin: '8px 0 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {events.map((ev, idx) => {
            const color = STAGE_COLOR[ev.stage] || '#94a3b8';
            return (
              <li
                key={idx}
                style={{
                  borderLeft: `3px solid ${color}`,
                  background: ev.added_by_replan ? 'rgba(244, 63, 94, 0.08)' : 'rgba(255,255,255,0.03)',
                  padding: '6px 10px',
                  borderRadius: '0 6px 6px 0',
                }}
              >
                <div style={{ color: '#e2e8f0', fontWeight: 600 }}>
                  {ev.title}
                  {ev.added_by_replan && (
                    <span style={{ marginLeft: '8px', color: '#fda4af', fontWeight: 700 }}>
                      ← agregado al replanificar (no estaba en el plan inicial)
                    </span>
                  )}
                </div>
                {ev.detail && (
                  <div style={{ color: '#94a3b8', fontFamily: 'var(--font-mono)', marginTop: '2px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {ev.detail}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
};
