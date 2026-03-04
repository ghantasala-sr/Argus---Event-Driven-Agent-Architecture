export default function InfraHealth({ data }) {
    if (!data) return null

    return (
        <div>
            {/* Lambda Functions */}
            <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.8px' }}>
                Lambda Functions
            </h3>
            <div className="infra-grid" style={{ marginBottom: '24px' }}>
                {(data.functions || []).map((fn) => {
                    const hasErrors = fn.errors_24h > 0
                    const statusClass = hasErrors ? 'warning' : 'active'
                    return (
                        <div key={fn.name} className="infra-item">
                            <div className="infra-name">
                                <span className={`status-dot ${statusClass}`} />
                                {fn.name}
                            </div>
                            <div className="infra-type">
                                {fn.runtime} • {fn.memory}MB • {fn.timeout}s timeout
                            </div>
                            <div className="infra-stats">
                                <div className="infra-stat">
                                    <span className="infra-stat-label">Invocations (24h)</span>
                                    <span className="infra-stat-value">{fn.invocations_24h}</span>
                                </div>
                                <div className="infra-stat">
                                    <span className="infra-stat-label">Errors (24h)</span>
                                    <span className="infra-stat-value" style={{ color: hasErrors ? 'var(--accent-red)' : undefined }}>
                                        {fn.errors_24h}
                                    </span>
                                </div>
                                <div className="infra-stat">
                                    <span className="infra-stat-label">Avg Duration</span>
                                    <span className="infra-stat-value">{fn.avg_duration_ms}ms</span>
                                </div>
                            </div>
                        </div>
                    )
                })}
            </div>

            {/* SQS Queues */}
            <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.8px' }}>
                SQS Queue Depth
            </h3>
            <div className="queue-bar-container" style={{ marginBottom: '24px' }}>
                {(data.queues || []).map((q) => {
                    const hasMessages = q.depth > 0
                    return (
                        <div key={q.name} className="queue-bar-item">
                            <div className="queue-bar-label">{q.name}</div>
                            <div className={`queue-bar ${hasMessages ? 'has-messages' : ''} ${q.is_dlq ? 'is-dlq' : ''}`}>
                                {q.depth}
                            </div>
                        </div>
                    )
                })}
            </div>

            {/* SNS Topics */}
            <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.8px' }}>
                SNS Topics
            </h3>
            <div className="infra-grid">
                {(data.topics || []).map((topic) => (
                    <div key={topic.name} className="infra-item">
                        <div className="infra-name">
                            <span className="status-dot active" />
                            {topic.name}
                        </div>
                        <div className="infra-type" style={{ fontSize: '10px', wordBreak: 'break-all' }}>
                            {topic.arn}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}
