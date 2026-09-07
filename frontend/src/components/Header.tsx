import React from 'react';
import { ShieldCheck, Cpu, Activity, User, UserCheck } from 'lucide-react';
import { HealthStatus } from '../types';

interface HeaderProps {
  currentTab: 'chat' | 'admin';
  setCurrentTab: (tab: 'chat' | 'admin') => void;
  health: HealthStatus | null;
  pendingCount: number;
}

export const Header: React.FC<HeaderProps> = ({
  currentTab,
  setCurrentTab,
  health,
  pendingCount,
}) => {
  return (
    <header className="glass-panel" style={{ margin: '16px', padding: '16px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
        
        {/* Logo & Brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div style={{
            width: '44px',
            height: '44px',
            borderRadius: '12px',
            background: 'linear-gradient(135deg, #6366f1 0%, #06b6d4 100%)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 4px 20px rgba(99, 102, 241, 0.3)'
          }}>
            <ShieldCheck size={26} color="#ffffff" />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <h1 style={{ fontSize: '1.25rem', fontWeight: 800, letterSpacing: '-0.02em', color: '#ffffff' }}>
                AegisBank <span style={{ color: '#818cf8' }}>AI</span>
              </h1>
              <span className="badge badge-indigo" style={{ fontSize: '0.65rem' }}>Defense-in-Depth</span>
            </div>
            <p style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              Sistema Multi-Agente Bancario & Gobernanza en GCP
            </p>
          </div>
        </div>

        {/* System Telemetry Badges */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          {health ? (
            <>
              <div className="badge badge-emerald" title="Backend Status">
                <Activity size={12} /> API Online
              </div>
              <div className="badge badge-cyan" title="Sanitizer & Guardrails Mode">
                <Cpu size={12} /> {health.security_mode.toUpperCase()}
              </div>
              <div className="badge badge-indigo" title="LangGraph Checkpointer">
                <span>💾 {health.checkpointer.toUpperCase()}</span>
              </div>
            </>
          ) : (
            <div className="badge badge-amber">
              <Activity size={12} /> Conectando al backend...
            </div>
          )}
        </div>

        {/* Tab Switcher: Client vs Compliance HITL Admin */}
        <div style={{
          display: 'flex',
          background: 'rgba(15, 23, 42, 0.8)',
          padding: '4px',
          borderRadius: '12px',
          border: '1px solid var(--border-subtle)',
          gap: '4px'
        }}>
          <button
            onClick={() => setCurrentTab('chat')}
            style={{
              padding: '8px 16px',
              borderRadius: '8px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: 600,
              fontSize: '0.85rem',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              transition: 'all 0.2s ease',
              background: currentTab === 'chat' ? 'var(--primary)' : 'transparent',
              color: currentTab === 'chat' ? '#ffffff' : '#94a3b8',
            }}
          >
            <User size={16} /> Cliente Banca
          </button>

          <button
            onClick={() => setCurrentTab('admin')}
            style={{
              padding: '8px 16px',
              borderRadius: '8px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: 600,
              fontSize: '0.85rem',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              position: 'relative',
              transition: 'all 0.2s ease',
              background: currentTab === 'admin' ? 'var(--primary)' : 'transparent',
              color: currentTab === 'admin' ? '#ffffff' : '#94a3b8',
            }}
          >
            <UserCheck size={16} /> Portal Cumplimiento (HITL)
            {pendingCount > 0 && (
              <span style={{
                position: 'absolute',
                top: '-4px',
                right: '-4px',
                background: '#ef4444',
                color: '#fff',
                borderRadius: '9999px',
                padding: '2px 6px',
                fontSize: '0.65rem',
                fontWeight: 700,
                boxShadow: '0 0 8px rgba(239, 68, 68, 0.6)'
              }}>
                {pendingCount}
              </span>
            )}
          </button>
        </div>

      </div>
    </header>
  );
};
