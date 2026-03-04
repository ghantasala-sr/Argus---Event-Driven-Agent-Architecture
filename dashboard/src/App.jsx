import { useState, useEffect, useCallback } from 'react'
import Overview from './components/Overview'
import ReviewsTable from './components/ReviewsTable'
import FindingsPanel from './components/FindingsPanel'
import InfraHealth from './components/InfraHealth'

const REFRESH_INTERVAL = 30000 // 30 seconds

export default function App() {
    const [overview, setOverview] = useState(null)
    const [reviews, setReviews] = useState([])
    const [infrastructure, setInfrastructure] = useState(null)
    const [selectedReview, setSelectedReview] = useState(null)
    const [findings, setFindings] = useState([])
    const [loading, setLoading] = useState(true)
    const [lastRefresh, setLastRefresh] = useState(null)

    const fetchData = useCallback(async () => {
        try {
            const [overviewRes, reviewsRes, infraRes] = await Promise.all([
                fetch('/api/overview').then(r => r.json()),
                fetch('/api/reviews').then(r => r.json()),
                fetch('/api/infrastructure').then(r => r.json()),
            ])
            setOverview(overviewRes)
            setReviews(reviewsRes.reviews || [])
            setInfrastructure(infraRes)
            setLastRefresh(new Date())
        } catch (err) {
            console.error('Failed to fetch dashboard data:', err)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchData()
        const interval = setInterval(fetchData, REFRESH_INTERVAL)
        return () => clearInterval(interval)
    }, [fetchData])

    const handleReviewClick = async (reviewId) => {
        if (selectedReview === reviewId) {
            setSelectedReview(null)
            setFindings([])
            return
        }
        setSelectedReview(reviewId)
        try {
            const res = await fetch(`/api/findings/${reviewId}`)
            const data = await res.json()
            setFindings(data.findings || [])
        } catch (err) {
            console.error('Failed to fetch findings:', err)
            setFindings([])
        }
    }

    const formatTime = (date) => {
        if (!date) return '—'
        return date.toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        })
    }

    return (
        <div className="dashboard">
            <header className="dashboard-header">
                <div className="dashboard-title">
                    <div className="dashboard-logo">A</div>
                    <h1>Argus Dashboard</h1>
                </div>
                <div className="header-meta">
                    <div className="refresh-indicator">
                        <span className="refresh-dot" />
                        <span>Auto-refresh 30s • Last: {formatTime(lastRefresh)}</span>
                    </div>
                    <button className="refresh-btn" onClick={fetchData}>
                        ↻ Refresh
                    </button>
                </div>
            </header>

            {loading ? (
                <div className="loading-container">
                    <div className="loading-spinner" />
                    <div className="loading-text">Connecting to AWS...</div>
                </div>
            ) : (
                <>
                    <Overview data={overview} />

                    <div className="main-grid">
                        <div className="glass-card full-width animate-in" style={{ animationDelay: '0.2s' }}>
                            <div className="card-header">
                                <h2>📋 Recent Reviews</h2>
                                <span className="card-badge" style={{
                                    background: 'var(--accent-blue-glow)',
                                    color: 'var(--accent-blue)',
                                    border: '1px solid rgba(59, 130, 246, 0.2)',
                                }}>
                                    {reviews.length} reviews
                                </span>
                            </div>
                            <ReviewsTable
                                reviews={reviews}
                                selectedReview={selectedReview}
                                onReviewClick={handleReviewClick}
                            />
                        </div>

                        {selectedReview && (
                            <div className="glass-card full-width animate-in">
                                <div className="card-header">
                                    <h2>🔍 Findings — {selectedReview}</h2>
                                    <span className="card-badge" style={{
                                        background: findings.length > 0 ? 'var(--severity-critical-bg)' : 'var(--accent-green-glow)',
                                        color: findings.length > 0 ? 'var(--severity-critical)' : 'var(--accent-green)',
                                        border: `1px solid ${findings.length > 0 ? 'rgba(239,68,68,0.2)' : 'rgba(16,185,129,0.2)'}`,
                                    }}>
                                        {findings.length} findings
                                    </span>
                                </div>
                                <FindingsPanel findings={findings} />
                            </div>
                        )}

                        <div className="glass-card full-width animate-in" style={{ animationDelay: '0.3s' }}>
                            <div className="card-header">
                                <h2>⚙️ Infrastructure Health</h2>
                                <span className="card-badge" style={{
                                    background: 'var(--accent-green-glow)',
                                    color: 'var(--accent-green)',
                                    border: '1px solid rgba(16, 185, 129, 0.2)',
                                }}>
                                    Live
                                </span>
                            </div>
                            <InfraHealth data={infrastructure} />
                        </div>
                    </div>
                </>
            )}
        </div>
    )
}
