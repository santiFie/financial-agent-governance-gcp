import React from 'react';
import { ShieldAlert, ShieldCheck, X, KeyRound, EyeOff, CheckCircle2 } from 'lucide-react';
import { SecurityMetadata } from '../types';

interface SecurityAuditModalProps {
  metadata: SecurityMetadata | null;
  rawText: string;
  onClose: () => void;
}

export const SecurityAuditModal: React.FC<SecurityAuditModalProps> = ({
  metadata,
  rawText,
  onClose,
}) => {
  if (!metadata) return null;

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      background: 'rgba(5, 8, 15, 0.85)',
      backdropFilter: 'blur(8px)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 9999,
      padding: '20px'
    }}>
      <div className="glass-panel" style={{
        maxWidth: '700px',
        width: '100%',
        maxHeight: '90vh',
        overflowY: 'auto',
        padding: '28px',
        position: 'relative',
        border: '1px solid rgba(99, 102, 241, 0.3)',
        boxShadow: '0 20px 50px rgba(0, 0, 0, 0.6)'
      }}>
        {/* Close button */}
        <button
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '20px',
            right: '20px',
            background: 'rgba(255,255,255,0.06)',
            border: 'none',
            borderRadius: '8px',
            color: '#94a3b8',
            cursor: 'pointer',
            padding: '6px'
          }}
        >
          <X size={20} />
        </button>

        {/* Title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: '10px',
            background: metadata.prompt_injection_flagged ? 'rgba(239, 68, 68, 0.2)' : 'rgba(99, 102, 241, 0.2)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            {metadata.prompt_injection_flagged ? (
              <ShieldAlert color="#f43f5e" size={22} />
            ) : (
              <ShieldCheck color="#818cf8" size={22} />
            )}
          </div>
          <div>
            <h2 style={{ fontSize: '1.2rem', fontWeight: 700 }}>
              Auditoría de Gobernanza y Sanitización (Defense-in-Depth)
            </h2>
            <p style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              Telemetría de inspección previa a la invocación de agentes LLM
            </p>
          </div>
        </div>

        {/* Status Pills */}
        <div style={{ display: 'flex', gap: '8px', marginBottom: '20px', flexWrap: 'wrap' }}>
          <span className={`badge ${metadata.prompt_injection_flagged ? 'badge-rose' : 'badge-emerald'}`}>
            Model Armor: {metadata.prompt_injection_flagged ? 'AMENAZA DETECTADA' : 'SEGURO (PASSED)'}
          </span>
          <span className={`badge ${metadata.pii_detected.length > 0 ? 'badge-amber' : 'badge-cyan'}`}>
            Cloud DLP: {metadata.pii_detected.length} InfoTypes Anonimizados
          </span>
          <span className="badge badge-indigo">
            Latencia: {metadata.processing_time_ms} ms
          </span>
        </div>

        {/* Comparison: Raw vs Sanitized */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginBottom: '24px' }}>
          <div>
            <label style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
              <EyeOff size={14} /> Entrada Cruda del Usuario (NUNCA enviada al LLM):
            </label>
            <div style={{
              background: 'rgba(15, 23, 42, 0.9)',
              padding: '12px 16px',
              borderRadius: '8px',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.85rem',
              border: '1px solid rgba(244, 63, 94, 0.2)',
              color: '#fda4af',
              wordBreak: 'break-word'
            }}>
              {rawText}
            </div>
          </div>

          <div>
            <label style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
              <CheckCircle2 size={14} /> Prompt Sanitizado / Anonimizado (Recibido por Gemini):
            </label>
            <div style={{
              background: 'rgba(15, 23, 42, 0.9)',
              padding: '12px 16px',
              borderRadius: '8px',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.85rem',
              border: '1px solid rgba(16, 185, 129, 0.3)',
              color: '#6ee7b7',
              wordBreak: 'break-word'
            }}>
              {metadata.sanitized_input}
            </div>
          </div>
        </div>

        {/* Tokens Mapping Table */}
        {metadata.pii_detected.length > 0 && (
          <div style={{ marginBottom: '20px' }}>
            <h4 style={{ fontSize: '0.9rem', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <KeyRound size={16} color="#fbbf24" /> Tokens Sustitutos Aislados en Vault Seguro:
            </h4>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)', textAlign: 'left', color: '#94a3b8' }}>
                  <th style={{ padding: '8px' }}>InfoType DLP</th>
                  <th style={{ padding: '8px' }}>Token Sustituto</th>
                  <th style={{ padding: '8px' }}>Aislamiento</th>
                </tr>
              </thead>
              <tbody>
                {metadata.pii_detected.map((pii, idx) => (
                  <tr key={idx} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <td style={{ padding: '8px', fontWeight: 600, color: '#e2e8f0' }}>{pii.info_type}</td>
                    <td style={{ padding: '8px', fontFamily: 'var(--font-mono)', color: '#38bdf8' }}>
                      {pii.token_assigned}
                    </td>
                    <td style={{ padding: '8px', color: '#10b981' }}>Vault Epímero Seguro</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }}>
          <button onClick={onClose} className="btn-primary" style={{ padding: '8px 20px' }}>
            Entendido
          </button>
        </div>
      </div>
    </div>
  );
};
