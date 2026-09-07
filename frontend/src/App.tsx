import React, { useState, useEffect } from 'react';
import { Header } from './components/Header';
import { ChatView } from './components/ChatView';
import { AdminDashboard } from './components/AdminDashboard';
import { SecurityAuditModal } from './components/SecurityAuditModal';
import {
  ChatMessage,
  BankAccount,
  PendingTransferInfo,
  HealthStatus,
  SecurityMetadata,
} from './types';
import {
  checkHealth,
  sendChatMessage,
  fetchPendingTransfers,
  approveTransfer,
  fetchUserAccounts,
} from './services/api';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<'chat' | 'admin'>('chat');
  const [sessionId] = useState<string>(() => {
    return 'sess_' + Math.random().toString(36).substring(2, 11);
  });
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [pendingTransfers, setPendingTransfers] = useState<PendingTransferInfo[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome_msg',
      sender: 'agent',
      text: '👋 ¡Hola Santiago! Soy tu Asistente Financiero con arquitectura segura **Defense-in-Depth**.\n\nPuedes consultarme tus saldos, tus últimos movimientos o solicitar una transferencia bancaria.\n\n*Nota de seguridad: Cualquier dato sensible (DNI, CBU, Tarjetas) será anonimizado por Cloud DLP antes de llegar al modelo de IA.*',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ]);
  const [loading, setLoading] = useState(false);

  // Security Audit Modal State
  const [auditMetadata, setAuditMetadata] = useState<SecurityMetadata | null>(null);
  const [auditRawText, setAuditRawText] = useState<string>('');

  // Initial Load & Health
  useEffect(() => {
    const loadInitialData = async () => {
      try {
        const h = await checkHealth();
        setHealth(h);
      } catch (err) {
        console.warn('Backend not yet reachable:', err);
      }

      try {
        const accData = await fetchUserAccounts();
        setAccounts(accData.accounts || []);
      } catch (err) {
        console.warn('Could not load accounts:', err);
      }

      try {
        const pending = await fetchPendingTransfers();
        setPendingTransfers(pending);
      } catch (err) {
        console.warn('Could not load pending transfers:', err);
      }
    };

    loadInitialData();

    // Polling for pending transfers every 6 seconds to update badge
    const interval = setInterval(async () => {
      try {
        const pending = await fetchPendingTransfers();
        setPendingTransfers(pending);
      } catch (err) {
        // ignore background poll errors
      }
    }, 6000);

    return () => clearInterval(interval);
  }, []);

  // Send message
  const handleSendMessage = async (text: string) => {
    const userMsgId = 'msg_' + Date.now();
    const newMsg: ChatMessage = {
      id: userMsgId,
      sender: 'user',
      text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, newMsg]);
    setLoading(true);

    try {
      const response = await sendChatMessage(text, sessionId);

      const agentMsg: ChatMessage = {
        id: 'agent_' + Date.now(),
        sender: 'agent',
        text: response.response,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        status: response.status,
        security_audit: response.security_audit,
        pending_transfer: response.pending_transfer,
      };

      setMessages((prev) => [...prev, agentMsg]);

      // If a transfer was staged, update pending list
      if (response.pending_transfer) {
        setPendingTransfers((prev) => [...prev, response.pending_transfer]);
      }
    } catch (err: any) {
      const errorMsg: ChatMessage = {
        id: 'err_' + Date.now(),
        sender: 'system',
        text: `❌ Error procesando tu solicitud: ${err.message || 'Error de conexión'}`,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  // Approve / Reject Transfer from Admin HITL
  const handleApprove = async (
    targetSessionId: string,
    txId: string,
    decision: 'APPROVED' | 'REJECTED',
    reason?: string
  ) => {
    const result = await approveTransfer(targetSessionId, txId, decision, 'compliance_officer_santi', reason);
    
    // Refresh pending list
    const updated = await fetchPendingTransfers();
    setPendingTransfers(updated);

    // Refresh accounts balance if approved
    const accData = await fetchUserAccounts();
    setAccounts(accData.accounts || []);

    // Also append the resumption message to chat if it corresponds to current session
    if (targetSessionId === sessionId) {
      const resumeMsg: ChatMessage = {
        id: 'resume_' + Date.now(),
        sender: 'agent',
        text: result.message,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        status: 'COMPLETED',
      };
      setMessages((prev) => [...prev, resumeMsg]);
    }
  };

  const openAuditModal = (metadata: SecurityMetadata, rawText: string) => {
    setAuditMetadata(metadata);
    setAuditRawText(rawText);
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Header
        currentTab={currentTab}
        setCurrentTab={setCurrentTab}
        health={health}
        pendingCount={pendingTransfers.length}
      />

      <main style={{ flex: 1 }}>
        {currentTab === 'chat' ? (
          <ChatView
            messages={messages}
            loading={loading}
            accounts={accounts}
            onSendMessage={handleSendMessage}
            onOpenAudit={openAuditModal}
            onGoToAdmin={() => setCurrentTab('admin')}
          />
        ) : (
          <AdminDashboard
            pendingTransfers={pendingTransfers}
            onApprove={handleApprove}
            onRefresh={async () => {
              const pending = await fetchPendingTransfers();
              setPendingTransfers(pending);
            }}
            loading={loading}
          />
        )}
      </main>

      <SecurityAuditModal
        metadata={auditMetadata}
        rawText={auditRawText}
        onClose={() => setAuditMetadata(null)}
      />
    </div>
  );
};

export default App;
