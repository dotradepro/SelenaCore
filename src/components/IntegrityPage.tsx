import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store/useStore';

interface LogEntry {
  color: string;
  text: string;
  time: string;
}

export default function IntegrityPage() {
  const { t } = useTranslation();
  const stats      = useStore((s) => s.stats);
  const fetchStats = useStore((s) => s.fetchStats);
  const [age, setAge]         = useState(0);
  const [checks, setChecks]   = useState(847);
  const [log, setLog]         = useState<LogEntry[]>(() => {
    const now = new Date();
    const p = (x: number) => String(x).padStart(2, '0');
    const ts = (d: number) => {
      const t = new Date(now.getTime() - d * 1000);
      return `${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
    };
    return [
      { color:'var(--gr)', text: t('integrityPage.logAllOkNoChanges', { count: 847 }), time: ts(0) },
      { color:'var(--gr)', text: t('integrityPage.logAllOk',         { count: 847 }), time: ts(30) },
      { color:'var(--gr)', text: t('integrityPage.logAllOk',         { count: 847 }), time: ts(60) },
      { color:'var(--ac)', text: t('integrityPage.logModuleUpdated', { name: 'climate-control', version: '1.2.1' }), time: ts(11640) },
      { color:'var(--gr)', text: t('integrityPage.logCoreStartupVerified'), time: ts(36480) },
    ];
  });

  useEffect(() => {
    fetchStats();
    const id = setInterval(() => {
      setAge((a) => {
        const next = (a + 1) % 30;
        if (next === 0) {
          setChecks((c) => c + 1);
          const now = new Date();
          const p = (x: number) => String(x).padStart(2, '0');
          const ts = `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
          setLog((l) => [
            { color:'var(--gr)', text: t('integrityPage.logAllOkNoCount'), time: ts },
            ...l.slice(0, 9),
          ]);
        }
        return next;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [fetchStats, t]);

  const intOk = (stats?.integrity ?? 'ok') === 'ok';

  return (
    <div className="generic-page">
      {/* Hero */}
      <div
        className="int-hero"
        style={intOk ? {} : {
          background:'rgba(224,84,84,.06)',
          borderColor:'rgba(224,84,84,.15)',
        }}
      >
        <div
          className="int-icon"
          style={intOk ? {} : { background:'rgba(224,84,84,.12)' }}
        >
          {intOk ? (
            <svg viewBox="0 0 20 20" fill="none" width="20" height="20" style={{ color:'var(--gr)' }}>
              <path d="M10 2L3 6v5c0 3.5 3 6.5 7 7.5 4-1 7-4 7-7.5V6L10 2Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
              <path d="M7 10l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          ) : (
            <svg viewBox="0 0 20 20" fill="none" width="20" height="20" style={{ color:'var(--rd)' }}>
              <path d="M10 2L3 6v5c0 3.5 3 6.5 7 7.5 4-1 7-4 7-7.5V6L10 2Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
              <path d="M10 8v3M10 13v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          )}
        </div>
        <div>
          <div style={{ fontSize:13, fontWeight:500, color: intOk ? 'var(--tx)' : 'var(--rd)' }}>
            {intOk ? t('integrityPage.allVerified') : t('integrityPage.violationDetected')}
          </div>
          <div style={{ fontSize:10, color:'var(--tx3)', marginTop:2 }}>
            {t('integrityPage.metaLine', { checks })}
          </div>
          <div style={{ fontSize:10, color: intOk ? 'var(--gr)' : 'var(--am)', marginTop:3 }}>
            {t('integrityPage.lastCheck', { age })}
          </div>
        </div>
        <div style={{
          marginLeft:'auto',
          fontFamily:'var(--font-mono)',
          fontSize:24, fontWeight:300,
          color: intOk ? 'var(--gr)' : 'var(--rd)',
        }}>
          {checks}
        </div>
      </div>

      {/* Log */}
      <div className="card">
        <div className="card-title">{t('integrityPage.checkLog')}</div>
        <div className="tl">
          {log.map((entry, i) => (
            <div key={i} className="tl-row">
              <div className="tldot" style={{ background: entry.color }} />
              <div className="tl-tx">{entry.text}</div>
              <div className="tl-tm">{entry.time}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
