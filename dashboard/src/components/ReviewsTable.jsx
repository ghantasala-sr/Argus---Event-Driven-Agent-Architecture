export default function ReviewsTable({ reviews, selectedReview, onReviewClick }) {
    if (!reviews || reviews.length === 0) {
        return (
            <div className="empty-state">
                <div className="empty-state-icon">📭</div>
                <div className="empty-state-text">No reviews yet</div>
                <div className="empty-state-sub">Open a PR on your monitored repo to trigger a review</div>
            </div>
        )
    }

    const formatDate = (ts) => {
        if (!ts) return '—'
        try {
            const d = new Date(ts)
            return d.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
            })
        } catch {
            return ts
        }
    }

    return (
        <table className="reviews-table">
            <thead>
                <tr>
                    <th>Repository</th>
                    <th>PR</th>
                    <th>Status</th>
                    <th>Files</th>
                    <th>Time</th>
                </tr>
            </thead>
            <tbody>
                {reviews.map((review) => (
                    <tr
                        key={review.review_id}
                        onClick={() => onReviewClick(review.review_id)}
                        style={{
                            background: selectedReview === review.review_id
                                ? 'rgba(59, 130, 246, 0.05)'
                                : undefined,
                        }}
                    >
                        <td>
                            <span className="repo-name">{review.repo || '—'}</span>
                        </td>
                        <td>
                            {review.pr_url ? (
                                <a
                                    href={review.pr_url}
                                    className="pr-link"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={(e) => e.stopPropagation()}
                                >
                                    #{review.pr_number}
                                </a>
                            ) : (
                                `#${review.pr_number || '—'}`
                            )}
                        </td>
                        <td>
                            <span className={`severity-badge ${review.status === 'complete' ? 'info' : 'warning'}`}>
                                {review.status || 'pending'}
                            </span>
                        </td>
                        <td>{review.files_count || 0}</td>
                        <td>{formatDate(review.timestamp)}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    )
}
