export default function FindingsPanel({ findings }) {
    if (!findings || findings.length === 0) {
        return (
            <div className="empty-state">
                <div className="empty-state-icon">✅</div>
                <div className="empty-state-text">No findings</div>
                <div className="empty-state-sub">This review has a clean bill of health</div>
            </div>
        )
    }

    const categoryLabels = {
        // Security
        sql_injection: 'SQL Injection',
        xss: 'XSS',
        command_injection: 'Command Injection',
        path_traversal: 'Path Traversal',
        auth_flaw: 'Auth Flaw',
        data_exposure: 'Data Exposure',
        ssrf: 'SSRF',
        deserialization: 'Deserialization',
        hardcoded_secret: 'Hardcoded Secret',
        // Style
        style: 'Coding Style',
        naming: 'Naming Convention',
        docstring: 'Documentation',
        readability: 'Readability',
        complexity: 'High Complexity',
        // Performance
        algorithm: 'Algorithmic Inefficiency',
        database: 'Database Bottleneck',
        memory: 'Memory Usage',
        network: 'Network/API',
        // Test
        missing_test: 'Missing Test',
        edge_case: 'Unhandled Edge Case',
        mocking: 'Poor Mocking',
        flaky: 'Flaky Pattern',
        // Generic
        other: 'Other',
    }

    return (
        <div>
            {findings.map((finding, i) => (
                <div key={i} className="finding-item animate-in" style={{ animationDelay: `${i * 0.05}s` }}>
                    <div className="finding-header">
                        <span className={`severity-badge ${finding.severity?.toLowerCase()}`}>
                            {finding.severity === 'critical' ? '🔴' : finding.severity === 'warning' ? '🟡' : '🔵'}
                            {' '}{finding.severity}
                        </span>
                        <span className="finding-category">
                            {categoryLabels[finding.category] || finding.category}
                        </span>
                        <span className="finding-location">
                            {finding.file}{finding.line > 0 ? `:${finding.line}` : ''}
                        </span>
                    </div>
                    <div className="finding-message">{finding.message}</div>
                    {finding.suggestion && (
                        <div className="finding-suggestion">
                            💡 {finding.suggestion}
                        </div>
                    )}
                </div>
            ))}
        </div>
    )
}
