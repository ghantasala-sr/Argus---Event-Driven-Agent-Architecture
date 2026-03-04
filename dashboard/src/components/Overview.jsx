export default function Overview({ data }) {
    if (!data) return null

    const cards = [
        {
            icon: '📊',
            value: data.total_reviews,
            label: 'Total Reviews',
            color: 'blue',
        },
        {
            icon: '🔍',
            value: data.total_findings,
            label: 'Total Findings',
            color: 'purple',
        },
        {
            icon: '🚨',
            value: data.findings_by_severity?.critical || 0,
            label: 'Critical Issues',
            color: 'red',
        },
        {
            icon: '⚡',
            value: Object.values(data.lambda_invocations_24h || {}).reduce((a, b) => a + b, 0),
            label: 'Invocations (24h)',
            color: 'green',
        },
    ]

    return (
        <div className="overview-grid">
            {cards.map((card, i) => (
                <div
                    key={card.label}
                    className={`glass-card overview-card ${card.color} animate-in`}
                    style={{ animationDelay: `${i * 0.05}s` }}
                >
                    <div className={`overview-icon ${card.color}`}>
                        {card.icon}
                    </div>
                    <div className="overview-value">{card.value}</div>
                    <div className="overview-label">{card.label}</div>
                </div>
            ))}
        </div>
    )
}
