import * as cdk from "aws-cdk-lib";
import * as sns from "aws-cdk-lib/aws-sns";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as subscriptions from "aws-cdk-lib/aws-sns-subscriptions";
import { Construct } from "constructs";

export interface MessagingStackProps extends cdk.StackProps {
    stage: string;
}

export class MessagingStack extends cdk.Stack {
    /** SNS topic for raw GitHub webhook events */
    public readonly prWebhookTopic: sns.Topic;
    /** SNS topic for structured parsed PR events (fan-out to review agents) */
    public readonly prParsedTopic: sns.Topic;
    /** SNS topic for agent review findings */
    public readonly reviewFindingsTopic: sns.Topic;
    /** SQS queue that feeds the Parser Agent */
    public readonly parseQueue: sqs.Queue;
    /** Dead letter queue for failed parse attempts */
    public readonly parseDlq: sqs.Queue;
    /** SQS queue that feeds the Security Agent */
    public readonly securityQueue: sqs.Queue;
    /** Dead letter queue for failed security scans */
    public readonly securityDlq: sqs.Queue;
    /** SQS queue that feeds the Style Agent */
    public readonly styleQueue: sqs.Queue;
    /** Dead letter queue for failed style scans */
    public readonly styleDlq: sqs.Queue;
    /** SQS queue that feeds the Performance Agent */
    public readonly performanceQueue: sqs.Queue;
    /** Dead letter queue for failed performance scans */
    public readonly performanceDlq: sqs.Queue;
    /** SQS queue that feeds the Test Agent */
    public readonly testQueue: sqs.Queue;
    /** Dead letter queue for failed test scans */
    public readonly testDlq: sqs.Queue;
    /** SNS topic for phase 4 completion events */
    public readonly reviewCompleteTopic: sns.Topic;
    /** SQS queue that feeds the Summary Agent */
    public readonly summaryQueue: sqs.Queue;
    /** Dead letter queue for failed summary generation */
    public readonly summaryDlq: sqs.Queue;
    /** SQS queue that feeds the LTM Writer (Learning) Agent */
    public readonly learnQueue: sqs.Queue;
    /** Dead letter queue for failed learning extraction */
    public readonly learnDlq: sqs.Queue;

    constructor(scope: Construct, id: string, props: MessagingStackProps) {
        super(scope, id, props);

        const { stage } = props;

        // --- SNS Topics ---

        this.prWebhookTopic = new sns.Topic(this, "PrWebhookTopic", {
            topicName: `argus-${stage}-pr-webhook`,
            displayName: "Argus PR Webhook Events",
        });

        this.prParsedTopic = new sns.Topic(this, "PrParsedTopic", {
            topicName: `argus-${stage}-pr-parsed`,
            displayName: "Argus Parsed PR Events (fan-out to review agents)",
        });

        this.reviewFindingsTopic = new sns.Topic(this, "ReviewFindingsTopic", {
            topicName: `argus-${stage}-review-findings`,
            displayName: "Argus Review Agent Findings",
        });

        this.reviewCompleteTopic = new sns.Topic(this, "ReviewCompleteTopic", {
            topicName: `argus-${stage}-review-complete`,
            displayName: "Argus Final Review Completed",
        });

        // --- SQS Queues ---

        // Dead Letter Queue for failed parse messages
        this.parseDlq = new sqs.Queue(this, "ParseDlq", {
            queueName: `argus-${stage}-parse-dlq`,
            retentionPeriod: cdk.Duration.days(14),
        });

        // Parser queue — receives raw webhook events, triggers Parser Lambda
        this.parseQueue = new sqs.Queue(this, "ParseQueue", {
            queueName: `argus-${stage}-parse-queue`,
            visibilityTimeout: cdk.Duration.seconds(300), // 5 min for Lambda processing
            retentionPeriod: cdk.Duration.days(4),
            deadLetterQueue: {
                queue: this.parseDlq,
                maxReceiveCount: 3, // 3 retries before DLQ
            },
        });

        // Dead Letter Queue for failed security scans
        this.securityDlq = new sqs.Queue(this, "SecurityDlq", {
            queueName: `argus-${stage}-security-dlq`,
            retentionPeriod: cdk.Duration.days(14),
        });

        // Security queue — receives parsed PR events, triggers Security Lambda
        this.securityQueue = new sqs.Queue(this, "SecurityQueue", {
            queueName: `argus-${stage}-security-queue`,
            visibilityTimeout: cdk.Duration.seconds(300),
            retentionPeriod: cdk.Duration.days(4),
            deadLetterQueue: {
                queue: this.securityDlq,
                maxReceiveCount: 3,
            },
        });

        // Style queue
        this.styleDlq = new sqs.Queue(this, "StyleDlq", {
            queueName: `argus-${stage}-style-dlq`,
            retentionPeriod: cdk.Duration.days(14),
        });
        this.styleQueue = new sqs.Queue(this, "StyleQueue", {
            queueName: `argus-${stage}-style-queue`,
            visibilityTimeout: cdk.Duration.seconds(300),
            retentionPeriod: cdk.Duration.days(4),
            deadLetterQueue: { queue: this.styleDlq, maxReceiveCount: 3 },
        });

        // Performance queue
        this.performanceDlq = new sqs.Queue(this, "PerformanceDlq", {
            queueName: `argus-${stage}-performance-dlq`,
            retentionPeriod: cdk.Duration.days(14),
        });
        this.performanceQueue = new sqs.Queue(this, "PerformanceQueue", {
            queueName: `argus-${stage}-performance-queue`,
            visibilityTimeout: cdk.Duration.seconds(300),
            retentionPeriod: cdk.Duration.days(4),
            deadLetterQueue: { queue: this.performanceDlq, maxReceiveCount: 3 },
        });

        // Test queue
        this.testDlq = new sqs.Queue(this, "TestDlq", {
            queueName: `argus-${stage}-test-dlq`,
            retentionPeriod: cdk.Duration.days(14),
        });
        this.testQueue = new sqs.Queue(this, "TestQueue", {
            queueName: `argus-${stage}-test-queue`,
            visibilityTimeout: cdk.Duration.seconds(300),
            retentionPeriod: cdk.Duration.days(4),
            deadLetterQueue: { queue: this.testDlq, maxReceiveCount: 3 },
        });

        // Summary queue
        this.summaryDlq = new sqs.Queue(this, "SummaryDlq", {
            queueName: `argus-${stage}-summary-dlq`,
            retentionPeriod: cdk.Duration.days(14),
        });
        this.summaryQueue = new sqs.Queue(this, "SummaryQueue", {
            queueName: `argus-${stage}-summary-queue`,
            visibilityTimeout: cdk.Duration.seconds(300),
            retentionPeriod: cdk.Duration.days(4),
            deadLetterQueue: { queue: this.summaryDlq, maxReceiveCount: 3 },
        });

        // LTM Learning queue
        this.learnDlq = new sqs.Queue(this, "LearnDlq", {
            queueName: `argus-${stage}-learn-dlq`,
            retentionPeriod: cdk.Duration.days(14),
        });
        this.learnQueue = new sqs.Queue(this, "LearnQueue", {
            queueName: `argus-${stage}-learn-queue`,
            visibilityTimeout: cdk.Duration.seconds(120),
            retentionPeriod: cdk.Duration.days(4),
            deadLetterQueue: { queue: this.learnDlq, maxReceiveCount: 3 },
        });

        // --- Subscriptions ---

        // Wire: pr.webhook SNS → parse-queue SQS
        this.prWebhookTopic.addSubscription(
            new subscriptions.SqsSubscription(this.parseQueue, {
                rawMessageDelivery: true,
            })
        );

        // Wire: pr.parsed SNS → security-queue SQS (fan-out for Phase 2)
        this.prParsedTopic.addSubscription(
            new subscriptions.SqsSubscription(this.securityQueue)
        );

        // Phase 3 concurrent agents fan-out
        this.prParsedTopic.addSubscription(new subscriptions.SqsSubscription(this.styleQueue));
        this.prParsedTopic.addSubscription(new subscriptions.SqsSubscription(this.performanceQueue));
        this.prParsedTopic.addSubscription(new subscriptions.SqsSubscription(this.testQueue));

        // Wire: review.findings SNS → summary-queue SQS
        this.reviewFindingsTopic.addSubscription(new subscriptions.SqsSubscription(this.summaryQueue));

        // Wire: review.complete SNS → learn-queue SQS
        this.reviewCompleteTopic.addSubscription(new subscriptions.SqsSubscription(this.learnQueue));

        // --- Outputs ---

        new cdk.CfnOutput(this, "PrWebhookTopicArn", {
            value: this.prWebhookTopic.topicArn,
            exportName: `${stage}-PrWebhookTopicArn`,
        });

        new cdk.CfnOutput(this, "PrParsedTopicArn", {
            value: this.prParsedTopic.topicArn,
            exportName: `${stage}-PrParsedTopicArn`,
        });

        new cdk.CfnOutput(this, "ReviewFindingsTopicArn", {
            value: this.reviewFindingsTopic.topicArn,
            exportName: `${stage}-ReviewFindingsTopicArn`,
        });

        new cdk.CfnOutput(this, "ParseQueueUrl", {
            value: this.parseQueue.queueUrl,
            exportName: `${stage}-ParseQueueUrl`,
        });

        new cdk.CfnOutput(this, "ParseDlqUrl", {
            value: this.parseDlq.queueUrl,
            exportName: `${stage}-ParseDlqUrl`,
        });

        new cdk.CfnOutput(this, "SecurityQueueUrl", {
            value: this.securityQueue.queueUrl,
            exportName: `${stage}-SecurityQueueUrl`,
        });

        new cdk.CfnOutput(this, "StyleQueueUrl", {
            value: this.styleQueue.queueUrl,
            exportName: `${stage}-StyleQueueUrl`,
        });

        new cdk.CfnOutput(this, "PerformanceQueueUrl", {
            value: this.performanceQueue.queueUrl,
            exportName: `${stage}-PerformanceQueueUrl`,
        });

        new cdk.CfnOutput(this, "TestQueueUrl", {
            value: this.testQueue.queueUrl,
            exportName: `${stage}-TestQueueUrl`,
        });

        new cdk.CfnOutput(this, "SummaryQueueUrl", {
            value: this.summaryQueue.queueUrl,
            exportName: `${stage}-SummaryQueueUrl`,
        });

        new cdk.CfnOutput(this, "LearnQueueUrl", {
            value: this.learnQueue.queueUrl,
            exportName: `${stage}-LearnQueueUrl`,
        });
    }
}
