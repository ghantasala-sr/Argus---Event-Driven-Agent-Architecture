import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";

export interface StorageStackProps extends cdk.StackProps {
    stage: string;
}

export class StorageStack extends cdk.Stack {
    /** Main DynamoDB table for reviews, findings, and team patterns */
    public readonly reviewsTable: dynamodb.Table;

    constructor(scope: Construct, id: string, props: StorageStackProps) {
        super(scope, id, props);

        const { stage } = props;

        // Single-table design:
        //   PK: pk (e.g., REV#<review_id>, TEAM#<repo_owner>)
        //   SK: sk (e.g., META, FINDING#security#<n>, PATTERN#<hash>)
        this.reviewsTable = new dynamodb.Table(this, "ReviewsTable", {
            tableName: `argus-${stage}-reviews`,
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            removalPolicy:
                stage === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
            pointInTimeRecovery: stage === "prod",
            timeToLiveAttribute: "ttl",
        });

        // GSI for querying reviews by status (e.g., "pending", "completed")
        this.reviewsTable.addGlobalSecondaryIndex({
            indexName: "gsi-status",
            partitionKey: { name: "status", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "created_at", type: dynamodb.AttributeType.STRING },
            projectionType: dynamodb.ProjectionType.KEYS_ONLY,
        });

        // --- Outputs ---

        new cdk.CfnOutput(this, "ReviewsTableName", {
            value: this.reviewsTable.tableName,
            exportName: `${stage}-ReviewsTableName`,
        });

        new cdk.CfnOutput(this, "ReviewsTableArn", {
            value: this.reviewsTable.tableArn,
            exportName: `${stage}-ReviewsTableArn`,
        });
    }
}
