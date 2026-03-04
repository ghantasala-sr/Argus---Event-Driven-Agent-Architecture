import * as cdk from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sns from "aws-cdk-lib/aws-sns";
import * as path from "path";
import { Construct } from "constructs";

export interface ApiStackProps extends cdk.StackProps {
    stage: string;
    prWebhookTopic: sns.Topic;
}

export class ApiStack extends cdk.Stack {
    /** The API Gateway REST API */
    public readonly api: apigateway.RestApi;

    constructor(scope: Construct, id: string, props: ApiStackProps) {
        super(scope, id, props);

        const { stage, prWebhookTopic } = props;

        // Lambda that validates GitHub webhook signature and publishes to SNS
        const webhookHandler = new lambda.Function(this, "WebhookHandler", {
            functionName: `argus-${stage}-webhook`,
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "webhook.handler.handler",
            code: lambda.Code.fromAsset(
                path.join(__dirname, "../../agents"),
                {
                    exclude: ["**/__pycache__", "**/*.pyc"],
                }
            ),
            timeout: cdk.Duration.seconds(30),
            memorySize: 256,
            environment: {
                STAGE: stage,
                PR_WEBHOOK_TOPIC_ARN: prWebhookTopic.topicArn,
                GITHUB_WEBHOOK_SECRET: "argus-webhook-secret", // SSM param name
            },
        });

        // Allow webhook Lambda to publish to SNS
        prWebhookTopic.grantPublish(webhookHandler);

        // REST API
        this.api = new apigateway.RestApi(this, "ArgusApi", {
            restApiName: `argus-${stage}-api`,
            description: "Argus webhook ingestion API",
            deployOptions: {
                stageName: stage,
            },
        });

        // POST /webhook
        const webhookResource = this.api.root.addResource("webhook");
        webhookResource.addMethod(
            "POST",
            new apigateway.LambdaIntegration(webhookHandler, {
                proxy: true,
            })
        );

        // --- Outputs ---

        new cdk.CfnOutput(this, "ApiUrl", {
            value: this.api.url,
            exportName: `${stage}-ApiUrl`,
        });

        new cdk.CfnOutput(this, "WebhookUrl", {
            value: `${this.api.url}webhook`,
            exportName: `${stage}-WebhookUrl`,
        });
    }
}
