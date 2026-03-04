import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaEventSources from "aws-cdk-lib/aws-lambda-event-sources";
import * as sns from "aws-cdk-lib/aws-sns";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { Construct } from "constructs";

export interface AgentsStackProps extends cdk.StackProps {
    stage: string;
    parseQueue: sqs.Queue;
    prParsedTopic: sns.Topic;
    reviewsTable: dynamodb.Table;
    securityQueue: sqs.Queue;
    styleQueue: sqs.Queue;
    performanceQueue: sqs.Queue;
    testQueue: sqs.Queue;
    reviewFindingsTopic: sns.Topic;
}

export class AgentsStack extends cdk.Stack {
    /** Parser Lambda function */
    public readonly parserFunction: lambda.Function;
    /** Security Agent Lambda function */
    public readonly securityFunction: lambda.Function;
    /** Style Agent Lambda function */
    public readonly styleFunction: lambda.Function;
    /** Performance Agent Lambda function */
    public readonly performanceFunction: lambda.Function;
    /** Test Agent Lambda function */
    public readonly testFunction: lambda.Function;

    constructor(scope: Construct, id: string, props: AgentsStackProps) {
        super(scope, id, props);

        const {
            stage,
            parseQueue,
            prParsedTopic,
            reviewsTable,
            securityQueue,
            styleQueue,
            performanceQueue,
            testQueue,
            reviewFindingsTopic,
        } = props;

        // --- Parser Agent Lambda ---
        this.parserFunction = new lambda.Function(this, "ParserFunction", {
            functionName: `argus-${stage}-parser`,
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "parser.handler.handler",
            code: lambda.Code.fromAsset(
                path.join(__dirname, "../../agents"),
                {
                    exclude: ["**/__pycache__", "**/*.pyc"],
                }
            ),
            timeout: cdk.Duration.seconds(120),
            memorySize: 512,
            environment: {
                STAGE: stage,
                TRANSPORT_TYPE: "sqs",
                INPUT_QUEUE_URL: parseQueue.queueUrl,
                PR_PARSED_TOPIC_ARN: prParsedTopic.topicArn,
                DYNAMODB_TABLE: reviewsTable.tableName,
                GITHUB_APP_ID: "2951763",
                GITHUB_PRIVATE_KEY_SECRET: "arn:aws:secretsmanager:us-east-1:219494607505:secret:argus/github-app-private-key-z4h819",
            },
        });

        // SQS trigger: parse-queue → Parser Lambda
        this.parserFunction.addEventSource(
            new lambdaEventSources.SqsEventSource(parseQueue, {
                batchSize: 1, // Process one PR at a time for reliability
                maxBatchingWindow: cdk.Duration.seconds(0),
            })
        );

        // Parser IAM permissions
        prParsedTopic.grantPublish(this.parserFunction);
        reviewsTable.grantWriteData(this.parserFunction);
        parseQueue.grantConsumeMessages(this.parserFunction);

        this.parserFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["secretsmanager:GetSecretValue"],
                resources: ["arn:aws:secretsmanager:us-east-1:219494607505:secret:argus/github-app-private-key-*"],
            })
        );

        // --- Security Agent Lambda ---
        this.securityFunction = new lambda.Function(this, "SecurityFunction", {
            functionName: `argus-${stage}-security`,
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "security.handler.handler",
            code: lambda.Code.fromAsset(
                path.join(__dirname, "../../agents"),
                {
                    exclude: ["**/__pycache__", "**/*.pyc"],
                }
            ),
            timeout: cdk.Duration.seconds(180), // Security analysis needs more time
            memorySize: 512,
            environment: {
                STAGE: stage,
                MODEL_ID: "amazon.nova-pro-v1:0",
                REVIEW_FINDINGS_TOPIC_ARN: reviewFindingsTopic.topicArn,
                DYNAMODB_TABLE: reviewsTable.tableName,
            },
        });

        // SQS trigger: security-queue → Security Lambda
        this.securityFunction.addEventSource(
            new lambdaEventSources.SqsEventSource(securityQueue, {
                batchSize: 1,
                maxBatchingWindow: cdk.Duration.seconds(0),
            })
        );

        // Security IAM permissions
        reviewFindingsTopic.grantPublish(this.securityFunction);
        reviewsTable.grantWriteData(this.securityFunction);
        securityQueue.grantConsumeMessages(this.securityFunction);

        // Bedrock InvokeModel permission
        this.securityFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["bedrock:InvokeModel"],
                resources: ["arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-*"],
            })
        );

        // --- Style Agent Lambda ---
        this.styleFunction = new lambda.Function(this, "StyleFunction", {
            functionName: `argus-${stage}-style`,
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "style.handler.handler",
            code: lambda.Code.fromAsset(
                path.join(__dirname, "../../agents"),
                { exclude: ["**/__pycache__", "**/*.pyc"] }
            ),
            timeout: cdk.Duration.seconds(60), // Style check is fast
            memorySize: 512,
            environment: {
                STAGE: stage,
                MODEL_ID: "amazon.nova-micro-v1:0", // Cheap/fast
                REVIEW_FINDINGS_TOPIC_ARN: reviewFindingsTopic.topicArn,
                DYNAMODB_TABLE: reviewsTable.tableName,
            },
        });
        this.styleFunction.addEventSource(new lambdaEventSources.SqsEventSource(styleQueue, { batchSize: 1, maxBatchingWindow: cdk.Duration.seconds(0) }));
        reviewFindingsTopic.grantPublish(this.styleFunction);
        reviewsTable.grantWriteData(this.styleFunction);
        styleQueue.grantConsumeMessages(this.styleFunction);
        this.styleFunction.addToRolePolicy(new iam.PolicyStatement({ actions: ["bedrock:InvokeModel"], resources: ["arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-*"] }));

        // --- Performance Agent Lambda ---
        this.performanceFunction = new lambda.Function(this, "PerformanceFunction", {
            functionName: `argus-${stage}-performance`,
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "performance.handler.handler",
            code: lambda.Code.fromAsset(
                path.join(__dirname, "../../agents"),
                { exclude: ["**/__pycache__", "**/*.pyc"] }
            ),
            timeout: cdk.Duration.seconds(180), // Deep reasoning
            memorySize: 512,
            environment: {
                STAGE: stage,
                MODEL_ID: "amazon.nova-pro-v1:0", // Pro model
                REVIEW_FINDINGS_TOPIC_ARN: reviewFindingsTopic.topicArn,
                DYNAMODB_TABLE: reviewsTable.tableName,
            },
        });
        this.performanceFunction.addEventSource(new lambdaEventSources.SqsEventSource(performanceQueue, { batchSize: 1, maxBatchingWindow: cdk.Duration.seconds(0) }));
        reviewFindingsTopic.grantPublish(this.performanceFunction);
        reviewsTable.grantWriteData(this.performanceFunction);
        performanceQueue.grantConsumeMessages(this.performanceFunction);
        this.performanceFunction.addToRolePolicy(new iam.PolicyStatement({ actions: ["bedrock:InvokeModel"], resources: ["arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-*"] }));

        // --- Test Agent Lambda ---
        this.testFunction = new lambda.Function(this, "TestFunction", {
            functionName: `argus-${stage}-test`,
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "test.handler.handler",
            code: lambda.Code.fromAsset(
                path.join(__dirname, "../../agents"),
                { exclude: ["**/__pycache__", "**/*.pyc"] }
            ),
            timeout: cdk.Duration.seconds(60),
            memorySize: 512,
            environment: {
                STAGE: stage,
                MODEL_ID: "amazon.nova-micro-v1:0",
                REVIEW_FINDINGS_TOPIC_ARN: reviewFindingsTopic.topicArn,
                DYNAMODB_TABLE: reviewsTable.tableName,
            },
        });
        this.testFunction.addEventSource(new lambdaEventSources.SqsEventSource(testQueue, { batchSize: 1, maxBatchingWindow: cdk.Duration.seconds(0) }));
        reviewFindingsTopic.grantPublish(this.testFunction);
        reviewsTable.grantWriteData(this.testFunction);
        testQueue.grantConsumeMessages(this.testFunction);
        this.testFunction.addToRolePolicy(new iam.PolicyStatement({ actions: ["bedrock:InvokeModel"], resources: ["arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-*"] }));

        // --- Outputs ---

        new cdk.CfnOutput(this, "ParserFunctionName", {
            value: this.parserFunction.functionName,
            exportName: `${stage}-ParserFunctionName`,
        });

        new cdk.CfnOutput(this, "ParserFunctionArn", {
            value: this.parserFunction.functionArn,
            exportName: `${stage}-ParserFunctionArn`,
        });

        new cdk.CfnOutput(this, "SecurityFunctionName", {
            value: this.securityFunction.functionName,
            exportName: `${stage}-SecurityFunctionName`,
        });

        new cdk.CfnOutput(this, "SecurityFunctionArn", {
            value: this.securityFunction.functionArn,
            exportName: `${stage}-SecurityFunctionArn`,
        });

        // Add outputs for new functions purely for reference/logging if desired
        new cdk.CfnOutput(this, "StyleFunctionArn", { value: this.styleFunction.functionArn });
        new cdk.CfnOutput(this, "PerformanceFunctionArn", { value: this.performanceFunction.functionArn });
        new cdk.CfnOutput(this, "TestFunctionArn", { value: this.testFunction.functionArn });
    }
}
