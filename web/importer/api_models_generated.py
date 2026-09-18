"""Generated from canonical EasyImports OpenAPI. DO NOT EDIT."""

from __future__ import annotations

GENERATOR = 'datamodel-code-generator/0.48.0'
OPENAPI_SHA256 = '4d3411b669f19c265beade6b8a2877da087931663e6b360ed6c1816eb209ae86'

from datetime import date
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel


class AbandonRunRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(title='Expected Revision')]


class ConnectionId(RootModel[str]):
    root: Annotated[str, Field(max_length=128, min_length=1, title='Connection Id')]


class AccountListImportOptions(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    crm_account_multi_match_policy: Annotated[
        Literal['review', 'quarantine', 'use_recommended'] | None,
        Field(title='Crm Account Multi Match Policy'),
    ] = 'review'
    df_view: Annotated[
        Literal['final', 'final_with_extras'] | None, Field(title='Df View')
    ] = 'final'
    interaction_mode: Annotated[
        Literal['interactive'], Field(title='Interaction Mode')
    ] = 'interactive'
    list_duplicate_policy: Annotated[
        Literal['surface', 'quarantine', 'drop_repeats'] | None,
        Field(title='List Duplicate Policy'),
    ] = 'surface'


class AccountListMaximumModes(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    account_provisioning: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Account Provisioning'),
    ] = 'disabled'
    account_writes: Annotated[Literal['disabled'], Field(title='Account Writes')] = (
        'disabled'
    )
    delivery: Annotated[
        Literal['disabled', 'preview', 'execute'] | None, Field(title='Delivery')
    ] = 'disabled'
    reference_acquisition: Annotated[
        Literal['disabled', 'execute'] | None, Field(title='Reference Acquisition')
    ] = 'disabled'


class AccountListTrackCatalog(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    account_provisioning: Annotated[
        list[Literal['disabled', 'preview', 'dry_run', 'execute']],
        Field(title='Account Provisioning'),
    ]
    account_writes: Annotated[list[str], Field(title='Account Writes')]
    delivery: Annotated[
        list[Literal['disabled', 'preview', 'execute']], Field(title='Delivery')
    ]
    reference_acquisition: Annotated[
        list[Literal['disabled', 'execute']], Field(title='Reference Acquisition')
    ]


class Accounts(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Accounts')]


class AccountListUploads(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    accounts: Annotated[Accounts | None, Field(title='Accounts')] = None
    raw_list: Annotated[str, Field(min_length=1, title='Raw List')]


class AccountProvisionCandidateResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    affected_person_count: Annotated[int, Field(ge=1, title='Affected Person Count')]
    company_identity: Annotated[str, Field(title='Company Identity')]
    identity_domain: Annotated[str | None, Field(title='Identity Domain')] = None
    origin: Annotated[str, Field(title='Origin')]
    requirement_id: Annotated[str, Field(title='Requirement Id')]
    source_row_ids: Annotated[list[str] | None, Field(title='Source Row Ids')] = None


class AmendReviewedDispositionEntry(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    action: Annotated[
        Literal['approve', 'override_survivor', 'decline', 'quarantine'],
        Field(title='Action'),
    ]
    advanced_review: Annotated[bool | None, Field(title='Advanced Review')] = False
    group_id: Annotated[str, Field(min_length=1, title='Group Id')]
    selected_survivor_id: Annotated[str | None, Field(title='Selected Survivor Id')] = (
        None
    )


class AmendReviewedDispositionWindowRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decisions: Annotated[
        list[AmendReviewedDispositionEntry],
        Field(max_length=25, min_length=1, title='Decisions'),
    ]
    expected_decision_set_content_digest: Annotated[
        str, Field(min_length=1, title='Expected Decision Set Content Digest')
    ]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    reviewed_result_content_digest: Annotated[
        str, Field(min_length=1, title='Reviewed Result Content Digest')
    ]
    window_token: Annotated[str, Field(min_length=1, title='Window Token')]


class AmendReviewedDispositionWindowResultBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_set_content_digest: Annotated[
        str, Field(min_length=1, title='Decision Set Content Digest')
    ]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    prior_decision_set_invalidated: Annotated[
        bool, Field(title='Prior Decision Set Invalidated')
    ]
    reviewed_result_content_digest: Annotated[
        str, Field(min_length=1, title='Reviewed Result Content Digest')
    ]


class ApiMutationStatusResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    http_status: Annotated[int | None, Field(title='Http Status')] = None
    mutation_id: Annotated[str, Field(title='Mutation Id')]
    mutation_kind: Annotated[str, Field(title='Mutation Kind')]
    response: Annotated[dict[str, Any] | None, Field(title='Response')] = None
    retry_after_seconds: Annotated[
        int | None, Field(ge=1, le=30, title='Retry After Seconds')
    ] = 2
    retryable: Annotated[bool | None, Field(title='Retryable')] = False
    status: Annotated[Literal['pending', 'completed'], Field(title='Status')]


class ArtifactResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    artifact_id: Annotated[str, Field(title='Artifact Id')]
    availability: Annotated[str, Field(title='Availability')]
    byte_count: Annotated[int | None, Field(title='Byte Count')]
    columns: Annotated[list[str], Field(title='Columns')]
    content_digest: Annotated[str, Field(title='Content Digest')]
    download_url: Annotated[str | None, Field(title='Download Url')]
    filename: Annotated[str, Field(title='Filename')]
    kind: Annotated[str, Field(title='Kind')]
    name: Annotated[str, Field(title='Name')]
    row_count: Annotated[int, Field(title='Row Count')]


class AutoDispositionRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    auto_merge_min_confidence: Annotated[
        int, Field(ge=90, le=100, title='Auto Merge Min Confidence')
    ]
    command_kind: Annotated[
        Literal['submit_duplicate_auto_disposition'], Field(title='Command Kind')
    ]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]


class AutoDispositionResultBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    applied_revision: Annotated[int, Field(ge=0, title='Applied Revision')]
    auto_approved_group_count: Annotated[
        int, Field(ge=0, title='Auto Approved Group Count')
    ]
    auto_approved_group_ids: Annotated[
        list[str], Field(title='Auto Approved Group Ids')
    ]
    auto_merge_min_confidence: Annotated[
        int, Field(ge=90, le=100, title='Auto Merge Min Confidence')
    ]
    command_kind: Annotated[
        Literal['submit_duplicate_auto_disposition'], Field(title='Command Kind')
    ]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    manual_queue_empty: Annotated[bool, Field(title='Manual Queue Empty')]
    manual_remaining_group_count: Annotated[
        int, Field(ge=0, title='Manual Remaining Group Count')
    ]
    result_digest: Annotated[str, Field(min_length=1, title='Result Digest')]
    review_ready: Annotated[bool, Field(title='Review Ready')]
    run_id: Annotated[str, Field(min_length=1, title='Run Id')]
    total_group_count: Annotated[int, Field(ge=0, title='Total Group Count')]


class BindDecisionSetHandoffRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_set_handoff_id: Annotated[
        str, Field(min_length=1, title='Decision Set Handoff Id')
    ]
    review_run_id: Annotated[str, Field(min_length=1, title='Review Run Id')]


class XlsxSheetIndex(RootModel[int]):
    root: Annotated[int, Field(ge=0, title='Xlsx Sheet Index')]


class BodyRegisterUploadV1UploadsPost(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    csv_encoding: Annotated[str | None, Field(title='Csv Encoding')] = None
    file: Annotated[bytes, Field(description='CSV or XLSX content', title='File')]
    xlsx_sheet_index: Annotated[
        XlsxSheetIndex | None, Field(title='Xlsx Sheet Index')
    ] = None
    xlsx_sheet_name: Annotated[str | None, Field(title='Xlsx Sheet Name')] = None


class ObservedName(RootModel[str]):
    root: Annotated[str, Field(max_length=512, title='Observed Name')]


class CampaignMatchResolutionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    campaign_id: Annotated[
        str, Field(max_length=18, min_length=15, title='Campaign Id')
    ]
    connection_id: Annotated[
        str, Field(max_length=128, min_length=1, title='Connection Id')
    ]
    match_key: Annotated[str, Field(max_length=512, min_length=1, title='Match Key')]
    match_mode: Annotated[Literal['name_exact'], Field(title='Match Mode')] = (
        'name_exact'
    )
    observed_name: Annotated[ObservedName | None, Field(title='Observed Name')] = None
    resolved_at: Annotated[str, Field(max_length=64, min_length=1, title='Resolved At')]
    selection_source: Annotated[
        Literal['unique_match', 'operator_pick'], Field(title='Selection Source')
    ]


class CampaignIdColumn(RootModel[str]):
    root: Annotated[
        str, Field(max_length=128, min_length=1, title='Campaign Id Column')
    ]


class CampaignMatchColumn(RootModel[str]):
    root: Annotated[
        str, Field(max_length=128, min_length=1, title='Campaign Match Column')
    ]


class DefaultCampaignBinding(RootModel[str]):
    root: Annotated[
        str, Field(max_length=18, min_length=15, title='Default Campaign Binding')
    ]


class DefaultDesiredStatus(RootModel[str]):
    root: Annotated[
        str, Field(max_length=255, min_length=1, title='Default Desired Status')
    ]


class MemberStatusColumn(RootModel[str]):
    root: Annotated[
        str, Field(max_length=128, min_length=1, title='Member Status Column')
    ]


class CampaignMemberPolicyResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    campaign_id_column: Annotated[
        CampaignIdColumn | None, Field(title='Campaign Id Column')
    ] = None
    campaign_match_column: Annotated[
        CampaignMatchColumn | None, Field(title='Campaign Match Column')
    ] = None
    campaign_match_resolutions: Annotated[
        list[CampaignMatchResolutionResource] | None,
        Field(title='Campaign Match Resolutions'),
    ] = None
    default_campaign_binding: Annotated[
        DefaultCampaignBinding | None, Field(title='Default Campaign Binding')
    ] = None
    default_desired_status: Annotated[
        DefaultDesiredStatus | None, Field(title='Default Desired Status')
    ] = None
    member_status_column: Annotated[
        MemberStatusColumn | None, Field(title='Member Status Column')
    ] = None
    policy_version: Annotated[
        Literal['campaign_member_policy.v2'], Field(title='Policy Version')
    ] = 'campaign_member_policy.v2'


class ColumnMappingAtomicReviewRow(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    mapping_choice_id: Annotated[str, Field(min_length=1, title='Mapping Choice Id')]
    source_ordinal: Annotated[int, Field(ge=0, title='Source Ordinal')]


class ColumnMappingDestination(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    catalog_id: Annotated[str | None, Field(title='Catalog Id')] = None
    connection_id: Annotated[str | None, Field(title='Connection Id')] = None
    destination_mode: Annotated[
        Literal['catalog', 'crm'], Field(title='Destination Mode')
    ]
    object_keys: Annotated[list[str] | None, Field(title='Object Keys')] = None
    operation_key: Annotated[str | None, Field(title='Operation Key')] = None
    provider_key: Annotated[str | None, Field(title='Provider Key')] = None
    schema_version: Annotated[str | None, Field(title='Schema Version')] = None


class ColumnMappingExpansionTargetResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    exclusive: Annotated[bool, Field(title='Exclusive')]
    target_key: Annotated[str, Field(title='Target Key')]


class ColumnMappingPlanConfirmRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    plan_content_digest: Annotated[
        str, Field(max_length=64, min_length=64, title='Plan Content Digest')
    ]


class ColumnMappingPlanRowPatchRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    mapping_choice_id: Annotated[str, Field(min_length=1, title='Mapping Choice Id')]


class ColumnMappingPlanRowResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    choice_origin: Annotated[
        Literal['auto_detected', 'operator_selected'] | None,
        Field(title='Choice Origin'),
    ] = None
    display_target_label: Annotated[str | None, Field(title='Display Target Label')] = (
        None
    )
    disposition: Annotated[
        Literal['unresolved', 'mapped', 'ignored'], Field(title='Disposition')
    ]
    mapping_choice_id: Annotated[str | None, Field(title='Mapping Choice Id')] = None
    source_header: Annotated[str, Field(title='Source Header')]
    source_ordinal: Annotated[int, Field(ge=0, title='Source Ordinal')]
    suggested_display_label: Annotated[
        str | None, Field(title='Suggested Display Label')
    ] = None
    suggested_mapping_choice_id: Annotated[
        str | None, Field(title='Suggested Mapping Choice Id')
    ] = None
    suggestion_reason: Annotated[str | None, Field(title='Suggestion Reason')] = None


class ColumnMappingSourceColumn(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    sample_values: Annotated[list[str] | None, Field(title='Sample Values')] = None
    source_header: Annotated[str, Field(title='Source Header')]
    source_ordinal: Annotated[int, Field(ge=0, title='Source Ordinal')]


class ColumnMappingWorkflowBind(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    destination_digest: Annotated[
        str, Field(max_length=64, min_length=64, title='Destination Digest')
    ]
    plan_content_digest: Annotated[
        str, Field(max_length=64, min_length=64, title='Plan Content Digest')
    ]
    plan_id: Annotated[str, Field(max_length=128, min_length=1, title='Plan Id')]
    source_schema_digest: Annotated[
        str, Field(max_length=64, min_length=64, title='Source Schema Digest')
    ]
    target_contract_digest: Annotated[
        str, Field(max_length=64, min_length=64, title='Target Contract Digest')
    ]


class CreateDecisionSetHandoffRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    review_handoff_id: Annotated[str, Field(min_length=1, title='Review Handoff Id')]
    source_run_id: Annotated[str, Field(min_length=1, title='Source Run Id')]


class CrmAppRegistrationResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    auth_mode: Annotated[str | None, Field(title='Auth Mode')] = None
    client_id_suffix: Annotated[str, Field(title='Client Id Suffix')]
    client_secret_configured: Annotated[bool, Field(title='Client Secret Configured')]
    created_at: Annotated[str, Field(title='Created At')]
    expected_hub_id_configured: Annotated[
        bool | None, Field(title='Expected Hub Id Configured')
    ] = False
    expected_org_id_configured: Annotated[
        bool | None, Field(title='Expected Org Id Configured')
    ] = False
    label: Annotated[str, Field(title='Label')]
    login_environment: Annotated[str, Field(title='Login Environment')]
    login_url: Annotated[str, Field(title='Login Url')]
    my_domain_host: Annotated[str | None, Field(title='My Domain Host')] = None
    provider_key: Annotated[str, Field(title='Provider Key')]
    redirect_uri: Annotated[str, Field(title='Redirect Uri')]
    updated_at: Annotated[str, Field(title='Updated At')]


class CrmAuthorizationDescriptor(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    authorization_url: Annotated[str | None, Field(title='Authorization Url')] = None
    callback_hint: Annotated[str, Field(title='Callback Hint')]
    mode: Annotated[Literal['oauth_code'], Field(title='Mode')]
    state: Annotated[str, Field(title='State')]


class CrmCampaignMemberStatusesResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    campaign_id: Annotated[str, Field(title='Campaign Id')]
    connection_id: Annotated[str, Field(title='Connection Id')]
    statuses: Annotated[list[str], Field(title='Statuses')]


class StartDate(RootModel[str]):
    root: Annotated[str, Field(max_length=32, title='Start Date')]


class Status(RootModel[str]):
    root: Annotated[str, Field(max_length=128, title='Status')]


class CrmCampaignResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: Annotated[str, Field(max_length=32, min_length=1, title='Id')]
    is_active: Annotated[bool, Field(title='Is Active')]
    name: Annotated[str, Field(max_length=255, min_length=0, title='Name')]
    start_date: Annotated[StartDate | None, Field(title='Start Date')] = None
    status: Annotated[Status | None, Field(title='Status')] = None


class CrmCampaignSearchResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    campaigns: Annotated[list[CrmCampaignResource], Field(title='Campaigns')]
    connection_id: Annotated[str, Field(title='Connection Id')]
    mode: Annotated[Literal['name_exact'], Field(title='Mode')]
    q: Annotated[str, Field(title='Q')]
    returned: Annotated[int, Field(ge=0, le=25, title='Returned')]
    too_many_matches: Annotated[bool, Field(title='Too Many Matches')]
    truncated: Annotated[bool, Field(title='Truncated')]


class CrmCandidateGroup(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    group_id: Annotated[str, Field(max_length=128, min_length=1, title='Group Id')]
    member_ids: Annotated[list[str], Field(min_length=2, title='Member Ids')]


class CrmConnectionLastReclaim(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    reclaim_mutation_id: Annotated[str, Field(title='Reclaim Mutation Id')]
    reclaim_offer_id: Annotated[str, Field(title='Reclaim Offer Id')]
    reclaimed_at: Annotated[str, Field(title='Reclaimed At')]


class CrmConnectionReclaimRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    reclaim_handle: Annotated[
        str, Field(max_length=256, min_length=1, title='Reclaim Handle')
    ]


class CrmConnectionStartRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    provider_key: Annotated[
        Literal['fake', 'salesforce', 'hubspot'], Field(title='Provider Key')
    ]


class CrmDuplicateJourneyResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    acquired_record_count: Annotated[int, Field(title='Acquired Record Count')]
    acquisition: Annotated[dict[str, Any] | None, Field(title='Acquisition')] = None
    auto_merge_min_confidence: Annotated[
        int | None, Field(title='Auto Merge Min Confidence')
    ] = None
    candidate_group_count: Annotated[int, Field(title='Candidate Group Count')]
    connection_id: Annotated[str, Field(title='Connection Id')]
    connection_removal_quarantine: Annotated[
        bool | None, Field(title='Connection Removal Quarantine')
    ] = None
    entity_family: Annotated[Literal['company', 'person'], Field(title='Entity Family')]
    journey_id: Annotated[str, Field(title='Journey Id')]
    matching_mode: Annotated[
        Literal['default', 'exact_only'] | None, Field(title='Matching Mode')
    ] = None
    next_steps: Annotated[list[str] | None, Field(title='Next Steps')] = None
    provider_key: Annotated[
        Literal['fake', 'salesforce', 'hubspot'], Field(title='Provider Key')
    ]
    provider_label: Annotated[str | None, Field(title='Provider Label')] = None
    run_id: Annotated[str, Field(title='Run Id')]
    source_mode: Annotated[
        Literal[
            'candidate_upload', 'acquire_all', 'selected_ids', 'uploaded_population'
        ],
        Field(title='Source Mode'),
    ]
    status: Annotated[str, Field(title='Status')]
    target_provider_id: Annotated[str, Field(title='Target Provider Id')]
    workflow_receipt: Annotated[
        dict[str, Any] | None, Field(title='Workflow Receipt')
    ] = None
    workflow_stage: Annotated[str | None, Field(title='Workflow Stage')] = None
    workflow_status: Annotated[str | None, Field(title='Workflow Status')] = None


class AutoMergeMinConfidence(RootModel[int]):
    root: Annotated[int, Field(ge=90, le=100, title='Auto Merge Min Confidence')]


class PopulationUploadId(RootModel[str]):
    root: Annotated[
        str, Field(max_length=128, min_length=1, title='Population Upload Id')
    ]


class QueryId(RootModel[str]):
    root: Annotated[str, Field(max_length=128, min_length=1, title='Query Id')]


class QueryResultDigest(RootModel[str]):
    root: Annotated[
        str, Field(max_length=128, min_length=1, title='Query Result Digest')
    ]


class CrmDuplicateJourneyStartRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    analysis_as_of_date: Annotated[date | None, Field(title='Analysis As Of Date')] = (
        None
    )
    auto_merge_min_confidence: Annotated[
        AutoMergeMinConfidence | None, Field(title='Auto Merge Min Confidence')
    ] = None
    candidate_groups: Annotated[
        list[CrmCandidateGroup] | None, Field(title='Candidate Groups')
    ] = None
    connection_id: Annotated[
        str, Field(max_length=128, min_length=1, title='Connection Id')
    ]
    duplicate_execution_maximum: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Duplicate Execution Maximum'),
    ] = 'execute'
    entity_family: Annotated[Literal['company', 'person'], Field(title='Entity Family')]
    matching_mode: Annotated[
        Literal['default', 'exact_only'] | None, Field(title='Matching Mode')
    ] = 'default'
    population_upload_id: Annotated[
        PopulationUploadId | None, Field(title='Population Upload Id')
    ] = None
    query_id: Annotated[QueryId | None, Field(title='Query Id')] = None
    query_result_digest: Annotated[
        QueryResultDigest | None, Field(title='Query Result Digest')
    ] = None
    selected_ids: Annotated[list[str] | None, Field(title='Selected Ids')] = None
    source_mode: Annotated[
        Literal[
            'candidate_upload', 'acquire_all', 'selected_ids', 'uploaded_population'
        ],
        Field(title='Source Mode'),
    ]


class CrmEntityCapabilityResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    cross_object_resolution: Annotated[bool, Field(title='Cross Object Resolution')]
    same_object_merge: Annotated[bool, Field(title='Same Object Merge')]
    supported_member_types: Annotated[list[str], Field(title='Supported Member Types')]


class ReconnectAttemptId(RootModel[str]):
    root: Annotated[
        str, Field(max_length=128, min_length=1, title='Reconnect Attempt Id')
    ]


class CrmOAuthCompleteRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    authorization_code: Annotated[
        str, Field(max_length=512, min_length=1, title='Authorization Code')
    ]
    reconnect_attempt_id: Annotated[
        ReconnectAttemptId | None, Field(title='Reconnect Attempt Id')
    ] = None
    state: Annotated[str, Field(max_length=256, min_length=1, title='State')]


class CrmProviderResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    authorization_modes: Annotated[list[str], Field(title='Authorization Modes')]
    connectable: Annotated[Literal[True], Field(title='Connectable')] = True
    provider_key: Annotated[
        Literal['fake', 'salesforce', 'hubspot'], Field(title='Provider Key')
    ]
    provider_label: Annotated[str, Field(title='Provider Label')]


class CrmQueryDeleteResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    query_id: Annotated[str, Field(title='Query Id')]
    status: Annotated[Literal['deleted'], Field(title='Status')]


class CrmQueryResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    connection_id: Annotated[str, Field(title='Connection Id')]
    connection_removal_quarantine: Annotated[
        bool | None, Field(title='Connection Removal Quarantine')
    ] = None
    mode: Annotated[
        Literal['by_ids', 'registered_filter_template'], Field(title='Mode')
    ]
    next_steps: Annotated[list[str] | None, Field(title='Next Steps')] = None
    object_family: Annotated[
        Literal['companies', 'people'], Field(title='Object Family')
    ]
    provider_key: Annotated[
        Literal['fake', 'salesforce', 'hubspot'], Field(title='Provider Key')
    ]
    provider_label: Annotated[str | None, Field(title='Provider Label')] = None
    query_id: Annotated[str, Field(title='Query Id')]
    result_digest: Annotated[str | None, Field(title='Result Digest')] = None
    row_count: Annotated[int | None, Field(title='Row Count')] = None
    run_id: Annotated[str, Field(title='Run Id')]
    snapshot_id: Annotated[str | None, Field(title='Snapshot Id')] = None
    status: Annotated[str, Field(title='Status')]
    target_provider_id: Annotated[str, Field(title='Target Provider Id')]
    workflow_receipt: Annotated[
        dict[str, Any] | None, Field(title='Workflow Receipt')
    ] = None
    workflow_stage: Annotated[str | None, Field(title='Workflow Stage')] = None
    workflow_status: Annotated[str | None, Field(title='Workflow Status')] = None


class TemplateId(RootModel[str]):
    root: Annotated[str, Field(max_length=256, min_length=1, title='Template Id')]


class TemplateVersion(RootModel[int]):
    root: Annotated[int, Field(ge=1, title='Template Version')]


class CrmQueryStartRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    connection_id: Annotated[
        str, Field(max_length=128, min_length=1, title='Connection Id')
    ]
    field_projection: Annotated[list[str] | None, Field(title='Field Projection')] = (
        None
    )
    max_rows: Annotated[int | None, Field(ge=1, le=50000, title='Max Rows')] = 50000
    max_stored_bytes: Annotated[
        int | None, Field(ge=1, le=64000000, title='Max Stored Bytes')
    ] = 64000000
    mode: Annotated[
        Literal['by_ids', 'registered_filter_template'], Field(title='Mode')
    ]
    object_family: Annotated[
        Literal['companies', 'people'], Field(title='Object Family')
    ]
    record_ids: Annotated[list[str] | None, Field(title='Record Ids')] = None
    template_id: Annotated[TemplateId | None, Field(title='Template Id')] = None
    template_params: Annotated[
        dict[str, str] | None, Field(title='Template Params')
    ] = None
    template_version: Annotated[
        TemplateVersion | None, Field(title='Template Version')
    ] = None


class CrmReconnectAbandonRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    reconnect_attempt_id: Annotated[
        str, Field(max_length=128, min_length=1, title='Reconnect Attempt Id')
    ]


class CrmRemovalDependent(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: Annotated[str, Field(title='Id')]
    type: Annotated[
        Literal[
            'duplicate-resolution journey',
            'CRM query',
            'CRM read grant',
            'workflow checkpoint',
        ],
        Field(title='Type'),
    ]


class CrmRemovalOrphan(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: Annotated[str, Field(title='Id')]
    reason: Annotated[Literal['missing_run_id'], Field(title='Reason')]
    type: Annotated[
        Literal[
            'duplicate-resolution journey',
            'CRM query',
            'CRM read grant',
            'workflow checkpoint',
        ],
        Field(title='Type'),
    ]


class CrmRemovalProgressResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    completed: Annotated[list[dict[str, Any]], Field(title='Completed')]
    pending: Annotated[list[dict[str, Any]], Field(title='Pending')]
    quarantined: Annotated[list[dict[str, Any]], Field(title='Quarantined')]


class CrmRemovalRun(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    checkpoint_revision: Annotated[int | None, Field(title='Checkpoint Revision')]
    dependents: Annotated[list[CrmRemovalDependent], Field(title='Dependents')]
    remote_outcome: Annotated[Literal['none', 'unknown'], Field(title='Remote Outcome')]
    run_id: Annotated[str, Field(title='Run Id')]
    run_status: Annotated[str, Field(title='Run Status')]


class CrmRemoveWithDependentsRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    confirmation_digest: Annotated[
        str, Field(max_length=128, min_length=1, title='Confirmation Digest')
    ]


class DecisionColumnResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    editable: Annotated[bool, Field(title='Editable')]
    label: Annotated[str, Field(title='Label')]
    name: Annotated[str, Field(title='Name')]
    visible: Annotated[bool, Field(title='Visible')]


class DecisionSetHandoffResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    binding_digest: Annotated[str, Field(title='Binding Digest')]
    decision_count: Annotated[int, Field(title='Decision Count')]
    entity: Annotated[Literal['account', 'person'], Field(title='Entity')]
    handoff_id: Annotated[str, Field(title='Handoff Id')]
    review_handoff_id: Annotated[str, Field(title='Review Handoff Id')]


class DecisionSetHandoffResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_set_handoff: DecisionSetHandoffResource


class DeliveryManifestResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    artifact_count: Annotated[int, Field(title='Artifact Count')]
    collision_policy: Annotated[
        Literal['error', 'increment'], Field(title='Collision Policy')
    ]
    logical_manifest_digest: Annotated[str, Field(title='Logical Manifest Digest')]
    namespace_contract: Annotated[str, Field(title='Namespace Contract')]
    physical_manifest_digest: Annotated[
        str | None, Field(title='Physical Manifest Digest')
    ] = None
    plan_digest: Annotated[str, Field(title='Plan Digest')]
    scalar_contract: Annotated[str, Field(title='Scalar Contract')]
    serializer_contract: Annotated[str, Field(title='Serializer Contract')]
    status: Annotated[Literal['planned', 'verified'], Field(title='Status')]
    target_fingerprint: Annotated[str, Field(title='Target Fingerprint')]


class DuplicateAnalysisProgress(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    completed_count: Annotated[int, Field(ge=0, title='Completed Count')]
    count_unit: Annotated[
        Literal['candidate_pairs', 'groups', 'stage'], Field(title='Count Unit')
    ]
    review_groups_ready: Annotated[int, Field(ge=0, title='Review Groups Ready')]
    review_groups_total: Annotated[int, Field(ge=0, title='Review Groups Total')]
    review_ready: Annotated[bool, Field(title='Review Ready')]
    review_window_ready: Annotated[bool, Field(title='Review Window Ready')]
    stage: Annotated[
        Literal[
            'routing_domain_groups',
            'building_evidence',
            'finalizing_groups',
            'ranking_survivors',
            'building_recommendations',
            'building_approval_bundle',
            'building_review_materials',
            'complete',
        ],
        Field(title='Stage'),
    ]
    total_count: Annotated[int, Field(ge=0, title='Total Count')]


class DuplicateExecutionCycleCommandRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_continuation_generation: Annotated[
        int, Field(ge=0, title='Expected Continuation Generation')
    ]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]


class DuplicateExecutionExceptionAggregates(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    deferred_count: Annotated[int, Field(ge=0, title='Deferred Count')]
    failed_count: Annotated[int, Field(ge=0, title='Failed Count')]
    merged_count: Annotated[int, Field(ge=0, title='Merged Count')]
    needs_attention_count: Annotated[int, Field(ge=0, title='Needs Attention Count')]
    verified_unmerged_count: Annotated[
        int, Field(ge=0, title='Verified Unmerged Count')
    ]


class DuplicateExecutionExceptionRow(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    group_execution_status: Annotated[
        str | None, Field(title='Group Execution Status')
    ] = None
    group_id: Annotated[str, Field(min_length=1, title='Group Id')]
    populate_status: Annotated[str | None, Field(title='Populate Status')] = None
    reason_code: Annotated[str | None, Field(title='Reason Code')] = None
    sanitized_display: Annotated[str, Field(min_length=1, title='Sanitized Display')]


class ActiveRecordScope(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Active Record Scope')]


class ConnectionId1(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Connection Id')]


class PopulationUploadId1(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Population Upload Id')]


class DuplicateReadGrantCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    active_record_scope: Annotated[
        ActiveRecordScope | None, Field(title='Active Record Scope')
    ] = None
    auto_merge_min_confidence: Annotated[
        AutoMergeMinConfidence | None, Field(title='Auto Merge Min Confidence')
    ] = None
    connection_id: Annotated[ConnectionId1 | None, Field(title='Connection Id')] = None
    entity_family: Annotated[
        Literal['company', 'person'] | None, Field(title='Entity Family')
    ] = None
    population_source: Annotated[
        Literal['crm_scan', 'uploaded_population'], Field(title='Population Source')
    ]
    population_upload_id: Annotated[
        PopulationUploadId1 | None, Field(title='Population Upload Id')
    ] = None


class DuplicateReadGrantResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    active_record_scope: Annotated[str | None, Field(title='Active Record Scope')] = (
        None
    )
    applied_at: Annotated[str | None, Field(title='Applied At')] = None
    applied_run_id: Annotated[str | None, Field(title='Applied Run Id')] = None
    auto_merge_min_confidence: Annotated[
        int | None, Field(title='Auto Merge Min Confidence')
    ] = None
    connection_id: Annotated[str, Field(title='Connection Id')]
    created_at: Annotated[str, Field(title='Created At')]
    entity_family: Annotated[Literal['company', 'person'], Field(title='Entity Family')]
    execution_source_mode: Annotated[
        Literal['acquire_all', 'candidate_ids'], Field(title='Execution Source Mode')
    ]
    grant_contract: Annotated[str, Field(title='Grant Contract')]
    grant_digest: Annotated[str, Field(title='Grant Digest')]
    grant_id: Annotated[str, Field(title='Grant Id')]
    idempotency_key: Annotated[str, Field(title='Idempotency Key')]
    inspect_digest: Annotated[str, Field(title='Inspect Digest')]
    mapping_digest: Annotated[str | None, Field(title='Mapping Digest')] = None
    permits_crm_reads_only: Annotated[
        Literal[True], Field(title='Permits Crm Reads Only')
    ]
    population_source: Annotated[
        Literal['crm_scan', 'uploaded_population'], Field(title='Population Source')
    ]
    population_upload_id: Annotated[str | None, Field(title='Population Upload Id')] = (
        None
    )
    record_id_count: Annotated[int | None, Field(title='Record Id Count')] = None
    record_id_digest: Annotated[str | None, Field(title='Record Id Digest')] = None
    schema_version: Annotated[str, Field(title='Schema Version')]
    tenant_binding_digest: Annotated[str, Field(title='Tenant Binding Digest')]
    upload_id: Annotated[str | None, Field(title='Upload Id')] = None


class DuplicateResolutionMaximumModes(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    delivery: Annotated[
        Literal['disabled', 'preview', 'execute'] | None, Field(title='Delivery')
    ] = 'disabled'
    duplicate_execution: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Duplicate Execution'),
    ] = 'disabled'
    reference_acquisition: Annotated[
        Literal['disabled', 'execute'] | None, Field(title='Reference Acquisition')
    ] = 'disabled'


class DuplicateResolutionTrackCatalog(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    delivery: Annotated[
        list[Literal['disabled', 'preview', 'execute']], Field(title='Delivery')
    ]
    duplicate_execution: Annotated[
        list[Literal['disabled', 'preview', 'dry_run', 'execute']],
        Field(title='Duplicate Execution'),
    ]
    reference_acquisition: Annotated[
        list[Literal['disabled', 'execute']], Field(title='Reference Acquisition')
    ]


class CanonicalRecords(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Canonical Records')]


class Opportunities(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Opportunities')]


class RelatedContacts(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Related Contacts')]


class RelatedLeads(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Related Leads')]


class DuplicateResolutionUploads(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    canonical_records: Annotated[
        CanonicalRecords | None, Field(title='Canonical Records')
    ] = None
    opportunities: Annotated[Opportunities | None, Field(title='Opportunities')] = None
    related_contacts: Annotated[
        RelatedContacts | None, Field(title='Related Contacts')
    ] = None
    related_leads: Annotated[RelatedLeads | None, Field(title='Related Leads')] = None


class DuplicateReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    action: Annotated[
        Literal[
            'approve', 'decline', 'quarantine', 'override_survivor', 'finish_for_now'
        ],
        Field(title='Action'),
    ]
    advanced_review: Annotated[bool | None, Field(title='Advanced Review')] = False
    decided_at: Annotated[AwareDatetime, Field(title='Decided At')]
    decided_by: Annotated[str, Field(min_length=1, title='Decided By')]
    duplicate_group_id: Annotated[str, Field(min_length=1, title='Duplicate Group Id')]
    group_revision: Annotated[str, Field(min_length=1, title='Group Revision')]
    remaining_group_disposition: Annotated[
        Literal['abandon', 'export'] | None, Field(title='Remaining Group Disposition')
    ] = None
    selected_survivor_id: Annotated[str | None, Field(title='Selected Survivor Id')] = (
        None
    )


class DuplicateReviewProgressResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decided_group_count: Annotated[int, Field(title='Decided Group Count')]
    deferred_group_count: Annotated[int, Field(title='Deferred Group Count')]
    entity: Annotated[Literal['account', 'person'], Field(title='Entity')]
    finish_for_now_available: Annotated[bool, Field(title='Finish For Now Available')]
    finished_for_now: Annotated[bool, Field(title='Finished For Now')]
    remaining_group_count: Annotated[int, Field(title='Remaining Group Count')]
    remaining_groups_exported: Annotated[bool, Field(title='Remaining Groups Exported')]


class EffectAuthorizationCommandRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    confirmation: Annotated[str, Field(min_length=1, title='Confirmation')]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    intent_id: Annotated[str, Field(min_length=1, title='Intent Id')]
    maximum_mode: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Maximum Mode'),
    ]
    selected_mode: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Selected Mode'),
    ]
    target_fingerprint: Annotated[str, Field(min_length=1, title='Target Fingerprint')]
    target_provider_id: Annotated[str, Field(min_length=1, title='Target Provider Id')]
    track: Annotated[
        Literal[
            'reference_acquisition',
            'account_provisioning',
            'account_writes',
            'person_duplicate_resolution',
            'people_writes',
            'campaign_member_writes',
            'dataset_writes',
            'duplicate_execution',
            'delivery',
            'crm_query',
        ],
        Field(title='Track'),
    ]
    work_digest: Annotated[str, Field(min_length=1, title='Work Digest')]


class EffectAuthorizationGrantResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    command_id: Annotated[str, Field(title='Command Id')]
    confirmation: Annotated[str, Field(title='Confirmation')]
    contract: Annotated[str, Field(title='Contract')]
    intent_id: Annotated[str, Field(title='Intent Id')]
    maximum_mode: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Maximum Mode'),
    ]
    selected_mode: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Selected Mode'),
    ]
    target_fingerprint: Annotated[str, Field(title='Target Fingerprint')]
    target_provider_id: Annotated[str, Field(title='Target Provider Id')]
    track: Annotated[
        Literal[
            'reference_acquisition',
            'account_provisioning',
            'account_writes',
            'person_duplicate_resolution',
            'people_writes',
            'campaign_member_writes',
            'dataset_writes',
            'duplicate_execution',
            'delivery',
            'crm_query',
        ],
        Field(title='Track'),
    ]
    work_digest: Annotated[str, Field(title='Work Digest')]


class EffectIntentResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    confirmation: Annotated[str, Field(title='Confirmation')]
    effect_phase_id: Annotated[str, Field(title='Effect Phase Id')]
    gate_phase_id: Annotated[str, Field(title='Gate Phase Id')]
    intent_id: Annotated[str, Field(title='Intent Id')]
    maximum_mode: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Maximum Mode'),
    ]
    supported_modes: Annotated[
        list[Literal['disabled', 'preview', 'dry_run', 'execute']],
        Field(title='Supported Modes'),
    ]
    target_fingerprint: Annotated[str, Field(title='Target Fingerprint')]
    target_provider_id: Annotated[str, Field(title='Target Provider Id')]
    track: Annotated[
        Literal[
            'reference_acquisition',
            'account_provisioning',
            'account_writes',
            'person_duplicate_resolution',
            'people_writes',
            'campaign_member_writes',
            'dataset_writes',
            'duplicate_execution',
            'delivery',
            'crm_query',
        ],
        Field(title='Track'),
    ]
    work_digest: Annotated[str, Field(title='Work Digest')]


class EffectResumeCommandRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]


class EffectReviewResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    candidate_set_digest: Annotated[str, Field(title='Candidate Set Digest')]
    candidates: Annotated[
        list[AccountProvisionCandidateResource] | None, Field(title='Candidates')
    ] = None
    company_count: Annotated[int, Field(ge=0, title='Company Count')]
    contract: Annotated[str, Field(title='Contract')]
    intent_id: Annotated[str | None, Field(title='Intent Id')] = None
    kind: Annotated[Literal['account_provisioning_candidates'], Field(title='Kind')]
    person_count: Annotated[int, Field(ge=0, title='Person Count')]
    work_digest: Annotated[str | None, Field(title='Work Digest')] = None


class EndReviewEarlyRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    window_digest: Annotated[str, Field(min_length=1, title='Window Digest')]
    window_id: Annotated[str, Field(min_length=1, title='Window Id')]


class ExportArtifactTableResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    kind: Annotated[str, Field(title='Kind')]
    name: Annotated[str, Field(title='Name')]


class ProfileKey(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Profile Key')]


class ExportTableSelector(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    kind: Annotated[str, Field(min_length=1, title='Kind')]
    name: Annotated[str, Field(min_length=1, title='Name')]


class FieldMergeConsolidatePairResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    source_id: Annotated[str, Field(title='Source Id')]
    target_id: Annotated[str, Field(title='Target Id')]


class FieldMergeStepResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    field_keys: Annotated[list[str] | None, Field(title='Field Keys')] = None
    ordinal: Annotated[int, Field(title='Ordinal')]
    projection_digest: Annotated[str, Field(title='Projection Digest')]
    source_id: Annotated[str | None, Field(title='Source Id')] = None
    step_kind: Annotated[str, Field(title='Step Kind')]
    target_id: Annotated[str | None, Field(title='Target Id')] = None


class FinalizeMergePlanHandoffRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    review_handoff_id: Annotated[str, Field(min_length=1, title='Review Handoff Id')]
    source_run_id: Annotated[str, Field(min_length=1, title='Source Run Id')]


class GroupDispositionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    action_ids: Annotated[list[str], Field(title='Action Ids')]
    duplicate_group_id: Annotated[str, Field(title='Duplicate Group Id')]
    error_code: Annotated[str | None, Field(title='Error Code')]
    error_message: Annotated[str | None, Field(title='Error Message')]
    group_revision: Annotated[str | None, Field(title='Group Revision')]
    member_ids: Annotated[list[str], Field(title='Member Ids')]
    reason: Annotated[str | None, Field(title='Reason')]
    selected_survivor_id: Annotated[str | None, Field(title='Selected Survivor Id')]
    status: Annotated[
        Literal[
            'recommended',
            'review_required',
            'approved',
            'planned',
            'dry_run',
            'verified',
            'declined',
            'quarantined',
            'incomplete',
            'failed',
        ],
        Field(title='Status'),
    ]
    verified_action_ids: Annotated[list[str], Field(title='Verified Action Ids')]


class GroupedDecisionSelection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    group_id: Annotated[str, Field(min_length=1, title='Group Id')]
    selected_option_id: Annotated[str, Field(min_length=1, title='Selected Option Id')]


class AccessToken(RootModel[str]):
    root: Annotated[str, Field(max_length=8192, title='Access Token')]


class ClientId(RootModel[str]):
    root: Annotated[str, Field(max_length=256, title='Client Id')]


class ClientSecret(RootModel[str]):
    root: Annotated[str, Field(max_length=4096, title='Client Secret')]


class ExpectedHubId(RootModel[str]):
    root: Annotated[str, Field(max_length=32, title='Expected Hub Id')]


class RedirectUri(RootModel[str]):
    root: Annotated[str, Field(max_length=512, title='Redirect Uri')]


class HubSpotAppRegistrationCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    access_token: Annotated[AccessToken | None, Field(title='Access Token')] = None
    auth_mode: Annotated[Literal['oauth', 'private_app'], Field(title='Auth Mode')]
    client_id: Annotated[ClientId | None, Field(title='Client Id')] = None
    client_secret: Annotated[ClientSecret | None, Field(title='Client Secret')] = None
    expected_hub_id: Annotated[ExpectedHubId | None, Field(title='Expected Hub Id')] = (
        None
    )
    label: Annotated[str, Field(max_length=128, min_length=1, title='Label')]
    redirect_uri: Annotated[RedirectUri | None, Field(title='Redirect Uri')] = None


class HubSpotAppRegistrationRotateSecretRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    access_token: Annotated[AccessToken | None, Field(title='Access Token')] = None
    client_secret: Annotated[ClientSecret | None, Field(title='Client Secret')] = None


class ImpliedReferenceAuthorizationRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    run_id: Annotated[str, Field(min_length=1, title='Run Id')]


class ImpliedReferenceAuthorizationResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    authorization_receipt: Annotated[
        dict[str, Any], Field(title='Authorization Receipt')
    ]
    grant: DuplicateReadGrantResource
    run_id: Annotated[str, Field(title='Run Id')]
    selected_mode: Annotated[Literal['execute'], Field(title='Selected Mode')]
    track: Annotated[Literal['reference_acquisition'], Field(title='Track')]


class InvalidateMergePlanFreezeRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]


class JsonValue(
    RootModel[
        Union[
            dict[str, Optional["JsonValue"]],
            list[Optional["JsonValue"]],
            str,
            int,
            float,
            bool,
            None,
        ]
    ]
):
    root: Union[
        dict[str, Optional["JsonValue"]],
        list[Optional["JsonValue"]],
        str,
        int,
        float,
        bool,
        None,
    ]


class ConnectionId2(RootModel[str]):
    root: Annotated[str, Field(max_length=128, min_length=1, title='Connection Id')]


class ListImportMaximumModes(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    account_provisioning: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Account Provisioning'),
    ] = 'disabled'
    campaign_member_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Campaign Member Writes'),
    ] = 'disabled'
    delivery: Annotated[
        Literal['disabled', 'preview', 'execute'] | None, Field(title='Delivery')
    ] = 'disabled'
    people_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='People Writes'),
    ] = 'disabled'
    person_duplicate_resolution: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Person Duplicate Resolution'),
    ] = 'disabled'
    reference_acquisition: Annotated[
        Literal['disabled', 'execute'] | None, Field(title='Reference Acquisition')
    ] = 'disabled'


class ListImportOptions(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    batch_validation_policy: Annotated[
        Literal['quarantine', 'continue_with_invalid'] | None,
        Field(title='Batch Validation Policy'),
    ] = 'quarantine'
    blank_non_north_america_address: Annotated[
        bool | None, Field(title='Blank Non North America Address')
    ] = False
    blank_non_us_states: Annotated[bool | None, Field(title='Blank Non Us States')] = (
        None
    )
    contacts_only: Annotated[bool | None, Field(title='Contacts Only')] = False
    crm_account_multi_match_policy: Annotated[
        Literal['review', 'quarantine', 'use_recommended'] | None,
        Field(title='Crm Account Multi Match Policy'),
    ] = 'review'
    crm_match_duplicate_policy: Annotated[
        Literal['surface', 'quarantine', 'drop_repeats'] | None,
        Field(title='Crm Match Duplicate Policy'),
    ] = 'drop_repeats'
    df_view: Annotated[
        Literal['final', 'final_with_extras'] | None, Field(title='Df View')
    ] = 'final'
    fill_missing_emails: Annotated[bool | None, Field(title='Fill Missing Emails')] = (
        False
    )
    include_people_no_accounts_as_leads: Annotated[
        bool | None, Field(title='Include People No Accounts As Leads')
    ] = True
    interaction_mode: Annotated[
        Literal['interactive'], Field(title='Interaction Mode')
    ] = 'interactive'
    list_duplicate_policy: Annotated[
        Literal['surface', 'quarantine', 'drop_repeats'] | None,
        Field(title='List Duplicate Policy'),
    ] = 'surface'
    match_speed: Annotated[
        Literal['fast', 'mixed', 'thorough'] | None, Field(title='Match Speed')
    ] = None
    placeholder_account_id: Annotated[
        str | None, Field(title='Placeholder Account Id')
    ] = None
    placeholder_acct_id: Annotated[str | None, Field(title='Placeholder Acct Id')] = (
        None
    )
    preserve_existing_contact_emails: Annotated[
        bool | None, Field(title='Preserve Existing Contact Emails')
    ] = False
    quarantine_duplicate_candidates: Annotated[
        bool | None, Field(title='Quarantine Duplicate Candidates')
    ] = False
    resolve_surface_for_import: Annotated[
        bool | None, Field(title='Resolve Surface For Import')
    ] = False
    strict_reference_validation: Annotated[
        bool | None, Field(title='Strict Reference Validation')
    ] = False
    strict_validation: Annotated[bool | None, Field(title='Strict Validation')] = False
    validate_list_rows: Annotated[bool | None, Field(title='Validate List Rows')] = True
    validate_reference_rows: Annotated[
        bool | None, Field(title='Validate Reference Rows')
    ] = False


class ListImportTrackCatalog(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    account_provisioning: Annotated[
        list[Literal['disabled', 'preview', 'dry_run', 'execute']],
        Field(title='Account Provisioning'),
    ]
    campaign_member_writes: Annotated[
        list[Literal['disabled', 'preview', 'dry_run', 'execute']],
        Field(title='Campaign Member Writes'),
    ]
    delivery: Annotated[
        list[Literal['disabled', 'preview', 'execute']], Field(title='Delivery')
    ]
    people_writes: Annotated[
        list[Literal['disabled', 'preview', 'dry_run', 'execute']],
        Field(title='People Writes'),
    ]
    person_duplicate_resolution: Annotated[
        list[Literal['disabled', 'preview', 'dry_run', 'execute']],
        Field(title='Person Duplicate Resolution'),
    ]
    reference_acquisition: Annotated[
        list[Literal['disabled', 'execute']], Field(title='Reference Acquisition')
    ]


class CompanyExclusions(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Company Exclusions')]


class Contacts(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Contacts')]


class IndustryMapping(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Industry Mapping')]


class Leads(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Leads')]


class Territory(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Territory')]


class Users(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title='Users')]


class ListImportUploads(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    accounts: Annotated[Accounts | None, Field(title='Accounts')] = None
    company_exclusions: Annotated[
        CompanyExclusions | None, Field(title='Company Exclusions')
    ] = None
    contacts: Annotated[Contacts | None, Field(title='Contacts')] = None
    industry_mapping: Annotated[
        IndustryMapping | None, Field(title='Industry Mapping')
    ] = None
    leads: Annotated[Leads | None, Field(title='Leads')] = None
    raw_list: Annotated[str, Field(min_length=1, title='Raw List')]
    territory: Annotated[Territory | None, Field(title='Territory')] = None
    users: Annotated[Users | None, Field(title='Users')] = None


class DataRoot(RootModel[str]):
    root: Annotated[str, Field(max_length=1024, min_length=1, title='Data Root')]


class LocalStorageUpdateRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    data_root: Annotated[DataRoot | None, Field(title='Data Root')] = None
    restore_default: Annotated[bool | None, Field(title='Restore Default')] = False


class OversizedQuarantineComponentResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    component_id: Annotated[str, Field(min_length=1, title='Component Id')]
    member_ids: Annotated[list[str], Field(title='Member Ids')]
    reason: Annotated[
        Literal['component_exceeds_automatic_resolution_size'], Field(title='Reason')
    ]
    record_count: Annotated[int, Field(ge=1, title='Record Count')]


class PausedEffectDiagnostic(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    code: Annotated[
        Literal[
            'reference_acquisition_saved_progress_reset_required',
            'reference_acquisition_state_store_unavailable',
            'reference_acquisition_adapter_contract_failed',
            'secret_store_dpapi_protect_failed',
            'secret_store_dpapi_unprotect_failed',
            'secret_store_unavailable',
            'preflight_bound_exceeded',
            'paused_effect_unavailable',
        ],
        Field(title='Code'),
    ]
    error_type: Annotated[
        Literal[
            'ReferenceAcquisitionError',
            'SecretStoreError',
            'DuplicateExecutionError',
            'unavailable',
        ],
        Field(title='Error Type'),
    ]
    message: Annotated[str, Field(title='Message')]
    win32_code: Annotated[int | None, Field(title='Win32 Code')]
    win32_code_hex: Annotated[str | None, Field(title='Win32 Code Hex')]


class PersonReviewDecisionCommand(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_id: Annotated[str, Field(min_length=1, title='Decision Id')]
    decision_type: Annotated[
        Literal['person_duplicate_group_review'], Field(title='Decision Type')
    ]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    response: DuplicateReviewDecisionResponse


class PrepareReviewedResultRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]


class PrepareReviewedResultResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    command_kind: Annotated[
        Literal['prepare_duplicate_reviewed_result'], Field(title='Command Kind')
    ]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    review_run_id: Annotated[str, Field(min_length=1, title='Review Run Id')]
    reviewed_result_content_digest: Annotated[
        str, Field(min_length=1, title='Reviewed Result Content Digest')
    ]
    status: Annotated[Literal['complete'], Field(title='Status')] = 'complete'


class ProductScopedTrackCeilingsResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    account_provisioning: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Account Provisioning'),
    ]
    campaign_member_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Campaign Member Writes'),
    ]
    delivery: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'], Field(title='Delivery')
    ]
    duplicate_execution: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Duplicate Execution'),
    ]
    people_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='People Writes'),
    ]
    person_duplicate_resolution: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Person Duplicate Resolution'),
    ]
    reference_acquisition: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Reference Acquisition'),
    ]


class RawPopulationUploadCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    connection_id: Annotated[str, Field(min_length=1, title='Connection Id')]
    entity_family: Annotated[Literal['company', 'person'], Field(title='Entity Family')]
    upload_id: Annotated[str, Field(min_length=1, title='Upload Id')]


class RawPopulationUploadResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    byte_count: Annotated[int, Field(title='Byte Count')]
    confirmed_mapping_digest: Annotated[
        str | None, Field(title='Confirmed Mapping Digest')
    ] = None
    connection_id: Annotated[str, Field(title='Connection Id')]
    content_digest: Annotated[str, Field(title='Content Digest')]
    created_at: Annotated[str, Field(title='Created At')]
    entity_family: Annotated[Literal['company', 'person'], Field(title='Entity Family')]
    filename: Annotated[str, Field(title='Filename')]
    inspect_digest: Annotated[str, Field(title='Inspect Digest')]
    media_type: Annotated[str, Field(title='Media Type')]
    parser_contract: Annotated[str, Field(title='Parser Contract')]
    population_contract: Annotated[str, Field(title='Population Contract')]
    population_source: Annotated[
        Literal['uploaded_population'], Field(title='Population Source')
    ]
    population_upload_id: Annotated[str, Field(title='Population Upload Id')]
    preview_rows: Annotated[list[list[str | None]], Field(title='Preview Rows')]
    record_id_count: Annotated[int | None, Field(title='Record Id Count')] = None
    record_id_digest: Annotated[str | None, Field(title='Record Id Digest')] = None
    record_id_mapped: Annotated[bool, Field(title='Record Id Mapped')]
    requires_group_id: Annotated[Literal[False], Field(title='Requires Group Id')]
    requires_member_id: Annotated[Literal[False], Field(title='Requires Member Id')]
    row_count: Annotated[int, Field(title='Row Count')]
    schema_version: Annotated[str, Field(title='Schema Version')]
    source_column: Annotated[str | None, Field(title='Source Column')] = None
    source_headers: Annotated[list[str], Field(title='Source Headers')]
    status: Annotated[Literal['registered', 'mapped'], Field(title='Status')]
    suggested_source_column: Annotated[
        str | None, Field(title='Suggested Source Column')
    ] = None
    upload_id: Annotated[str, Field(title='Upload Id')]


class RecordIdMappingConfirmRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    source_column: Annotated[str, Field(min_length=1, title='Source Column')]


class RecordIdMappingResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    confirmed_mapping_digest: Annotated[str, Field(title='Confirmed Mapping Digest')]
    connection_id: Annotated[str, Field(title='Connection Id')]
    content_digest: Annotated[str, Field(title='Content Digest')]
    created_at: Annotated[str, Field(title='Created At')]
    entity_family: Annotated[Literal['company', 'person'], Field(title='Entity Family')]
    filename: Annotated[str | None, Field(title='Filename')] = None
    inspect_digest: Annotated[str, Field(title='Inspect Digest')]
    mapping_contract: Annotated[str, Field(title='Mapping Contract')]
    mapping_revision: Annotated[int, Field(title='Mapping Revision')]
    media_type: Annotated[str | None, Field(title='Media Type')] = None
    population_contract: Annotated[str, Field(title='Population Contract')]
    population_source: Annotated[
        Literal['uploaded_population'], Field(title='Population Source')
    ]
    population_upload_id: Annotated[str, Field(title='Population Upload Id')]
    record_id_count: Annotated[int, Field(title='Record Id Count')]
    record_id_digest: Annotated[str, Field(title='Record Id Digest')]
    record_ids: Annotated[list[str], Field(title='Record Ids')]
    role: Annotated[Literal['record_id'], Field(title='Role')]
    schema_version: Annotated[str, Field(title='Schema Version')]
    source_column: Annotated[str, Field(title='Source Column')]
    source_headers: Annotated[list[str], Field(title='Source Headers')]
    status: Annotated[Literal['confirmed'], Field(title='Status')]
    suggested_source_column: Annotated[
        str | None, Field(title='Suggested Source Column')
    ] = None
    upload_id: Annotated[str, Field(title='Upload Id')]


class ReferenceAcquisitionProgress(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    collected_count: Annotated[int, Field(ge=0, title='Collected Count')]


class ReferenceAcquisitionRecoveryRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]


class ReferenceAcquisitionRecoveryResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    recovery_contract: Annotated[
        Literal['easyimports.reference_acquisition.recovery.v1'],
        Field(title='Recovery Contract'),
    ]
    resume_dispatched: Annotated[Literal[True], Field(title='Resume Dispatched')]
    state_reset: Annotated[Literal[True], Field(title='State Reset')]


class ReviewCompleteResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    analyzed_record_count: Annotated[int, Field(ge=0, title='Analyzed Record Count')]
    decided_group_count: Annotated[int, Field(ge=0, title='Decided Group Count')]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    no_duplicate_groups_found: Annotated[bool, Field(title='No Duplicate Groups Found')]
    outcome: Annotated[Literal['review_complete'], Field(title='Outcome')] = (
        'review_complete'
    )
    remaining_group_count: Annotated[
        Literal[0], Field(title='Remaining Group Count')
    ] = 0
    review_contract: Annotated[
        Literal['easyimports.crm.duplicate_review_complete.v1'],
        Field(title='Review Contract'),
    ]
    terminal_message: Annotated[str | None, Field(title='Terminal Message')] = None
    total_group_count: Annotated[int, Field(ge=0, title='Total Group Count')]


class ReviewHandoffResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    binding_digest: Annotated[str, Field(title='Binding Digest')]
    entity: Annotated[Literal['account', 'person'], Field(title='Entity')]
    group_count: Annotated[int, Field(title='Group Count')]
    handoff_id: Annotated[str, Field(title='Handoff Id')]


class ReviewWindowDecisionEntryBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    action: Annotated[
        Literal['approve', 'override_survivor', 'decline', 'quarantine'],
        Field(title='Action'),
    ]
    advanced_review: Annotated[bool | None, Field(title='Advanced Review')] = False
    group_id: Annotated[str, Field(min_length=1, title='Group Id')]
    group_revision: Annotated[str, Field(min_length=1, title='Group Revision')]
    selected_survivor_id: Annotated[str | None, Field(title='Selected Survivor Id')] = (
        None
    )


class ReviewWindowMemberResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    display_fields: Annotated[dict[str, str], Field(title='Display Fields')]
    ranking_evidence: Annotated[
        dict[str, JsonValue | None] | None, Field(title='Ranking Evidence')
    ] = None
    recommended: Annotated[bool, Field(title='Recommended')]
    record_id: Annotated[str, Field(min_length=1, title='Record Id')]
    selected: Annotated[bool, Field(title='Selected')]
    survivor_eligible: Annotated[bool, Field(title='Survivor Eligible')]


class ReviewWindowNotReadyResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    outcome: Annotated[
        Literal['duplicate_review_window_not_ready'], Field(title='Outcome')
    ]
    review_contract: Annotated[
        Literal['easyimports.crm.duplicate_review_window_not_ready.v1'],
        Field(title='Review Contract'),
    ]
    review_groups_ready: Annotated[int, Field(ge=0, title='Review Groups Ready')]
    review_groups_total: Annotated[int, Field(ge=0, title='Review Groups Total')]


class ReviewWindowSubmitRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decisions: Annotated[
        list[ReviewWindowDecisionEntryBody],
        Field(max_length=5, min_length=1, title='Decisions'),
    ]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    window_digest: Annotated[str, Field(min_length=1, title='Window Digest')]
    window_id: Annotated[str, Field(min_length=1, title='Window Id')]


class ReviewedGroupMemberResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    is_loser: Annotated[bool, Field(title='Is Loser')]
    is_survivor: Annotated[bool, Field(title='Is Survivor')]
    recommended: Annotated[bool | None, Field(title='Recommended')] = False
    record_id: Annotated[str, Field(min_length=1, title='Record Id')]
    survivor_eligible: Annotated[bool | None, Field(title='Survivor Eligible')] = False


class ReviewedResultMaterializationProgress(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    message: Annotated[str | None, Field(title='Message')] = None
    review_contract: Annotated[
        Literal['easyimports.crm.duplicate_reviewed_result_progress.v1'],
        Field(title='Review Contract'),
    ]
    review_run_id: Annotated[str, Field(min_length=1, title='Review Run Id')]
    reviewed_result_content_digest: Annotated[
        str | None, Field(title='Reviewed Result Content Digest')
    ] = None
    status: Annotated[
        Literal['not_started', 'queued', 'running', 'complete', 'failed'],
        Field(title='Status'),
    ]
    work_digest: Annotated[str | None, Field(title='Work Digest')] = None


class ReviewedResultSummaryResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    analysis_work_digest: Annotated[str | None, Field(title='Analysis Work Digest')] = (
        None
    )
    analyzed_record_count: Annotated[int, Field(ge=0, title='Analyzed Record Count')]
    approved_merge_group_count: Annotated[
        int, Field(ge=0, title='Approved Merge Group Count')
    ]
    auto_approved_group_count: Annotated[
        int | None, Field(ge=0, title='Auto Approved Group Count')
    ] = 0
    complete: Annotated[bool, Field(title='Complete')]
    decided_group_count: Annotated[int, Field(ge=0, title='Decided Group Count')]
    decision_set_content_digest: Annotated[
        str, Field(min_length=1, title='Decision Set Content Digest')
    ]
    declined_group_count: Annotated[int, Field(ge=0, title='Declined Group Count')]
    entity: Annotated[Literal['account', 'person'], Field(title='Entity')]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    loser_count: Annotated[int, Field(ge=0, title='Loser Count')]
    mapping_digest: Annotated[str | None, Field(title='Mapping Digest')] = None
    merge_plan_frozen: Annotated[bool | None, Field(title='Merge Plan Frozen')] = False
    quarantined_group_count: Annotated[
        int, Field(ge=0, title='Quarantined Group Count')
    ]
    review_binding_digest: Annotated[
        str | None, Field(title='Review Binding Digest')
    ] = None
    review_contract: Annotated[
        Literal[
            'easyimports.crm.duplicate_reviewed_result.v1',
            'easyimports.crm.duplicate_reviewed_result.v2',
        ],
        Field(title='Review Contract'),
    ]
    review_handoff_id: Annotated[str | None, Field(title='Review Handoff Id')] = None
    review_run_id: Annotated[str, Field(min_length=1, title='Review Run Id')]
    reviewed_result_content_digest: Annotated[
        str, Field(min_length=1, title='Reviewed Result Content Digest')
    ]
    source_run_id: Annotated[str | None, Field(title='Source Run Id')] = None
    source_snapshot_digest: Annotated[
        str | None, Field(title='Source Snapshot Digest')
    ] = None
    summary_contract: Annotated[
        Literal['easyimports.crm.duplicate_reviewed_result_summary.v1'],
        Field(title='Summary Contract'),
    ]
    survivor_count: Annotated[int, Field(ge=0, title='Survivor Count')]
    total_group_count: Annotated[int, Field(ge=0, title='Total Group Count')]


class RowAccountabilityResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    double_counted_ids: Annotated[
        list[JsonValue | None], Field(title='Double Counted Ids')
    ]
    duplicate_output_ids: Annotated[
        list[JsonValue | None], Field(title='Duplicate Output Ids')
    ]
    excluded_row_count: Annotated[int, Field(title='Excluded Row Count')]
    failed_row_ids: Annotated[list[JsonValue | None], Field(title='Failed Row Ids')]
    missing_ids: Annotated[list[JsonValue | None], Field(title='Missing Ids')]
    original_row_ids: Annotated[list[JsonValue | None], Field(title='Original Row Ids')]
    output_row_ids: Annotated[list[JsonValue | None], Field(title='Output Row Ids')]
    reconciles: Annotated[bool, Field(title='Reconciles')]


class RunOutputPackageCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    layout_version: Annotated[
        Literal['run_output_package.v1', 'run_output_package.v2'] | None,
        Field(title='Layout Version'),
    ] = 'run_output_package.v1'


class RunOutputPackageIncludedFile(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    byte_count: Annotated[int, Field(title='Byte Count')]
    content_digest: Annotated[str, Field(title='Content Digest')]
    path: Annotated[str, Field(title='Path')]
    source_artifact_id: Annotated[str | None, Field(title='Source Artifact Id')] = None


class RunOutputPackageResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    byte_count: Annotated[int, Field(title='Byte Count')]
    content_digest: Annotated[str, Field(title='Content Digest')]
    created_at: Annotated[str, Field(title='Created At')]
    download_url: Annotated[str | None, Field(title='Download Url')] = None
    expires_at: Annotated[str, Field(title='Expires At')]
    included_files: Annotated[
        list[RunOutputPackageIncludedFile], Field(title='Included Files')
    ]
    layout_version: Annotated[
        Literal['run_output_package.v1', 'run_output_package.v2'],
        Field(title='Layout Version'),
    ]
    package_id: Annotated[str, Field(title='Package Id')]
    run_id: Annotated[str, Field(title='Run Id')]
    run_revision: Annotated[int, Field(title='Run Revision')]
    status: Annotated[Literal['available', 'deleted', 'expired'], Field(title='Status')]
    workflow_key: Annotated[str, Field(title='Workflow Key')]


class ExpectedOrgId(RootModel[str]):
    root: Annotated[str, Field(max_length=18, title='Expected Org Id')]


class MyDomainHost(RootModel[str]):
    root: Annotated[str, Field(max_length=256, title='My Domain Host')]


class SalesforceAppRegistrationCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    client_id: Annotated[str, Field(max_length=256, min_length=1, title='Client Id')]
    client_secret: Annotated[
        str, Field(max_length=4096, min_length=1, title='Client Secret')
    ]
    expected_org_id: Annotated[ExpectedOrgId | None, Field(title='Expected Org Id')] = (
        None
    )
    label: Annotated[str, Field(max_length=128, min_length=1, title='Label')]
    login_environment: Annotated[
        Literal['production', 'sandbox'], Field(title='Login Environment')
    ]
    my_domain_host: Annotated[MyDomainHost | None, Field(title='My Domain Host')] = None
    redirect_uri: Annotated[
        str, Field(max_length=512, min_length=1, title='Redirect Uri')
    ]


class SalesforceAppRegistrationRotateSecretRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    client_secret: Annotated[
        str, Field(max_length=4096, min_length=1, title='Client Secret')
    ]


class SecretVaultHealthResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decrypt_failed_count: Annotated[int, Field(ge=0, title='Decrypt Failed Count')]
    indexed_key_count: Annotated[int, Field(ge=0, title='Indexed Key Count')]


class SingleDatasetImportOptions(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    df_view: Annotated[
        Literal['final', 'final_with_extras'] | None, Field(title='Df View')
    ] = 'final_with_extras'
    list_duplicate_policy: Annotated[
        Literal['surface', 'quarantine', 'drop_repeats'] | None,
        Field(title='List Duplicate Policy'),
    ] = 'drop_repeats'


class SingleDatasetMaximumModes(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    dataset_writes: Annotated[Literal['disabled'], Field(title='Dataset Writes')] = (
        'disabled'
    )
    delivery: Annotated[
        Literal['disabled', 'preview', 'execute'] | None, Field(title='Delivery')
    ] = 'disabled'


class SingleDatasetTrackCatalog(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    dataset_writes: Annotated[list[str], Field(title='Dataset Writes')]
    delivery: Annotated[
        list[Literal['disabled', 'preview', 'execute']], Field(title='Delivery')
    ]


class SingleDatasetUploads(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    dataset: Annotated[str, Field(min_length=1, title='Dataset')]


class StartReviewWorkflowRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    review_handoff_id: Annotated[str, Field(min_length=1, title='Review Handoff Id')]


class TargetMaximumModes(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    account_provisioning: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Account Provisioning'),
    ]
    account_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Account Writes'),
    ]
    campaign_member_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Campaign Member Writes'),
    ]
    dataset_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Dataset Writes'),
    ]
    delivery: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'], Field(title='Delivery')
    ]
    duplicate_execution: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Duplicate Execution'),
    ]
    people_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='People Writes'),
    ]
    person_duplicate_resolution: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Person Duplicate Resolution'),
    ]
    reference_acquisition: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Reference Acquisition'),
    ]


class TargetResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    maximum_modes: TargetMaximumModes
    product_maximum_modes: Annotated[
        dict[str, ProductScopedTrackCeilingsResource] | None,
        Field(title='Product Maximum Modes'),
    ] = None
    target_provider_id: Annotated[str, Field(title='Target Provider Id')]


class TrackReceiptResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    authorization: Annotated[
        Literal['none', 'plan_only', 'dry_run', 'execute'], Field(title='Authorization')
    ]
    capability: Annotated[
        Literal[
            'crm_reference_acquisition',
            'account_creation',
            'account_mutation',
            'people_creation_and_update',
            'person_duplicate_execution',
            'account_duplicate_execution',
            'campaign_member_mutation',
            'dataset_mutation',
            'filesystem_delivery',
            'crm_query',
        ]
        | None,
        Field(title='Capability'),
    ]
    evidence: Annotated[dict[str, JsonValue | None] | None, Field(title='Evidence')]
    outcome: Annotated[
        Literal[
            'skipped',
            'planned',
            'dry_run',
            'succeeded',
            'completed_with_failures',
            'quarantined',
        ],
        Field(title='Outcome'),
    ]
    reason: Annotated[str | None, Field(title='Reason')]
    track: Annotated[
        Literal[
            'reference_acquisition',
            'account_provisioning',
            'account_writes',
            'person_duplicate_resolution',
            'people_writes',
            'campaign_member_writes',
            'dataset_writes',
            'duplicate_execution',
            'delivery',
            'crm_query',
        ],
        Field(title='Track'),
    ]


class UploadResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    byte_count: Annotated[int, Field(title='Byte Count')]
    columns: Annotated[list[str], Field(title='Columns')]
    content_digest: Annotated[str, Field(title='Content Digest')]
    created_at: Annotated[str, Field(title='Created At')]
    csv_encoding: Annotated[str | None, Field(title='Csv Encoding')]
    filename: Annotated[str, Field(title='Filename')]
    media_type: Annotated[str, Field(title='Media Type')]
    parser_contract: Annotated[str, Field(title='Parser Contract')]
    row_count: Annotated[int, Field(title='Row Count')]
    size_limit_bytes: Annotated[int, Field(title='Size Limit Bytes')]
    upload_id: Annotated[str, Field(title='Upload Id')]
    xlsx_sheet: Annotated[str | int | None, Field(title='Xlsx Sheet')]


class ValidationDecisionChanges(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    contact_email_final: Annotated[str | None, Field(title='Contact Email Final')] = (
        None
    )


class ValidationDecisionRow(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    changes: ValidationDecisionChanges | None = None
    option_id: Annotated[str, Field(min_length=1, title='Option Id')]


class ValidationError(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    loc: Annotated[list[str | int], Field(title='Location')]
    msg: Annotated[str, Field(title='Message')]
    type: Annotated[str, Field(title='Error Type')]


class WorkflowFailureResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    code: Annotated[
        Literal[
            'workflow_failed',
            'abandoned_by_operator',
            'crm_connection_removed_by_operator',
        ],
        Field(title='Code'),
    ]
    error_id: Annotated[str, Field(title='Error Id')]
    message: Annotated[str, Field(title='Message')]


class AccountListCatalogEntry(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    product_key: Annotated[
        Literal['easyimports.account_list_import'], Field(title='Product Key')
    ]
    tracks: AccountListTrackCatalog
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class AccountListImportCreate(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    column_mapping: ColumnMappingWorkflowBind
    connection_id: Annotated[ConnectionId | None, Field(title='Connection Id')] = None
    maximum_modes: AccountListMaximumModes | None = None
    options: AccountListImportOptions | None = None
    product_key: Annotated[
        Literal['easyimports.account_list_import'], Field(title='Product Key')
    ]
    target_provider_id: Annotated[
        str, Field(max_length=128, min_length=1, title='Target Provider Id')
    ]
    uploads: AccountListUploads


class AccountListTerminalEvidence(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    accountability: RowAccountabilityResource
    delivery_manifest: DeliveryManifestResource | None
    receipts: Annotated[list[TrackReceiptResource], Field(title='Receipts')]
    workflow_key: Annotated[
        Literal['easyimports.account_list_import'], Field(title='Workflow Key')
    ]
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class AccountReviewDecisionCommand(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_id: Annotated[str, Field(min_length=1, title='Decision Id')]
    decision_type: Annotated[
        Literal['account_duplicate_group_review'], Field(title='Decision Type')
    ]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    response: DuplicateReviewDecisionResponse


class AmendReviewedDispositionWindowResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    command_id: Annotated[str, Field(min_length=1, title='Command Id')]
    command_kind: Annotated[
        Literal['amend_duplicate_reviewed_disposition_window'],
        Field(title='Command Kind'),
    ]
    error_code: Annotated[str | None, Field(title='Error Code')] = None
    message: Annotated[str | None, Field(title='Message')] = None
    outcome: Annotated[Literal['accepted'], Field(title='Outcome')] = 'accepted'
    resource: Annotated[str, Field(min_length=1, title='Resource')]
    result: AmendReviewedDispositionWindowResultBody
    revision: Annotated[int, Field(ge=0, title='Revision')]
    run_id: Annotated[str, Field(min_length=1, title='Run Id')]
    stage: Annotated[str, Field(min_length=1, title='Stage')]
    workflow_status: Annotated[str, Field(min_length=1, title='Workflow Status')]


class ArtifactCollection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    artifacts: Annotated[list[ArtifactResource], Field(title='Artifacts')]


class ColumnMappingAtomicReviewRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    expected_plan_content_digest: Annotated[
        str, Field(max_length=64, min_length=64, title='Expected Plan Content Digest')
    ]
    intent: Annotated[Literal['save_draft', 'confirm'], Field(title='Intent')]
    rows: Annotated[list[ColumnMappingAtomicReviewRow] | None, Field(title='Rows')] = (
        None
    )
    schema_version: Annotated[
        Literal['column_mapping_atomic_review_command.v1'],
        Field(title='Schema Version'),
    ] = 'column_mapping_atomic_review_command.v1'


class ColumnMappingChoiceResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    choice_kind: Annotated[
        Literal['scalar', 'composite', 'ignore'], Field(title='Choice Kind')
    ]
    expansion_targets: Annotated[
        list[ColumnMappingExpansionTargetResource] | None,
        Field(title='Expansion Targets'),
    ] = None
    label: Annotated[str, Field(title='Label')]
    mapping_choice_id: Annotated[str, Field(title='Mapping Choice Id')]
    object_scope: Annotated[str | None, Field(title='Object Scope')] = None
    search_aliases: Annotated[list[str] | None, Field(title='Search Aliases')] = None


class ColumnMappingPlanCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    destination: ColumnMappingDestination
    run_auto_detect: Annotated[bool | None, Field(title='Run Auto Detect')] = True
    source_schema: Annotated[
        list[ColumnMappingSourceColumn], Field(min_length=1, title='Source Schema')
    ]


class ColumnMappingPlanResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    confirmed_at: Annotated[str | None, Field(title='Confirmed At')] = None
    confirmed_digests: Annotated[
        dict[str, str] | None, Field(title='Confirmed Digests')
    ] = None
    created_at: Annotated[str, Field(title='Created At')]
    destination: ColumnMappingDestination
    destination_digest: Annotated[str, Field(title='Destination Digest')]
    destination_mode: Annotated[
        Literal['catalog', 'crm'], Field(title='Destination Mode')
    ]
    owner_binding: Annotated[str, Field(title='Owner Binding')]
    plan_content_digest: Annotated[str, Field(title='Plan Content Digest')]
    plan_id: Annotated[str, Field(title='Plan Id')]
    rows: Annotated[list[ColumnMappingPlanRowResource], Field(title='Rows')]
    schema_version: Annotated[
        Literal['column_mapping_plan.v1'], Field(title='Schema Version')
    ]
    source_schema: Annotated[
        list[ColumnMappingSourceColumn], Field(title='Source Schema')
    ]
    source_schema_digest: Annotated[str, Field(title='Source Schema Digest')]
    status: Annotated[
        Literal['draft', 'confirmed', 'abandoned', 'invalidated'], Field(title='Status')
    ]
    target_contract_digest: Annotated[str, Field(title='Target Contract Digest')]


class CommandReceipt(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    command_id: Annotated[str, Field(title='Command Id')]
    command_kind: Annotated[str, Field(title='Command Kind')]
    error_code: Annotated[str | None, Field(title='Error Code')]
    message: Annotated[str | None, Field(title='Message')]
    outcome: Annotated[Literal['accepted', 'rejected'], Field(title='Outcome')]
    remote_outcome: Annotated[
        Literal['none', 'unknown'] | None, Field(title='Remote Outcome')
    ] = None
    resource: Annotated[str, Field(title='Resource')]
    result: Annotated[
        DecisionSetHandoffResult | ReferenceAcquisitionRecoveryResult | None,
        Field(title='Result'),
    ] = None
    revision: Annotated[int, Field(title='Revision')]
    run_id: Annotated[str, Field(title='Run Id')]
    stage: Annotated[str, Field(title='Stage')]
    workflow_status: Annotated[
        Literal[
            'ready',
            'running',
            'needs_decision',
            'awaiting_effect_authorization',
            'awaiting_review',
            'awaiting_execution_continuation',
            'paused_unknown',
            'paused_verification',
            'succeeded',
            'failed',
        ],
        Field(title='Workflow Status'),
    ]


class CrmAppRegistrationCollection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    registrations: Annotated[
        list[CrmAppRegistrationResource], Field(title='Registrations')
    ]


class CrmCampaignGetResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    campaign: CrmCampaignResource
    connection_id: Annotated[str, Field(title='Connection Id')]


class CrmDuplicateJourneyCollection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    journeys: Annotated[list[CrmDuplicateJourneyResource], Field(title='Journeys')]


class CrmMaximumAuthorizationResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    account_provisioning: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Account Provisioning'),
    ] = 'disabled'
    by_product: Annotated[
        dict[str, ProductScopedTrackCeilingsResource] | None, Field(title='By Product')
    ] = None
    campaign_member_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Campaign Member Writes'),
    ] = 'disabled'
    delivery: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Delivery'),
    ] = 'disabled'
    duplicate_execution: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Duplicate Execution'),
    ]
    people_writes: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='People Writes'),
    ] = 'disabled'
    person_duplicate_resolution: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Person Duplicate Resolution'),
    ] = 'disabled'
    reference_acquisition: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'],
        Field(title='Reference Acquisition'),
    ]


class CrmProviderCatalog(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    providers: Annotated[list[CrmProviderResource], Field(title='Providers')]


class CrmQueryCollection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    queries: Annotated[list[CrmQueryResource], Field(title='Queries')]


class CrmQueryRowsPage(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    columns: Annotated[list[str], Field(title='Columns')]
    has_more: Annotated[bool, Field(title='Has More')]
    next_cursor: Annotated[str | None, Field(title='Next Cursor')] = None
    offset: Annotated[int, Field(title='Offset')]
    page_size: Annotated[int, Field(title='Page Size')]
    query_id: Annotated[str, Field(title='Query Id')]
    result_digest: Annotated[str, Field(title='Result Digest')]
    returned: Annotated[int, Field(title='Returned')]
    rows: Annotated[list[list[JsonValue | None]], Field(title='Rows')]
    snapshot_id: Annotated[str, Field(title='Snapshot Id')]
    total_rows: Annotated[int, Field(title='Total Rows')]


class CrmRemovalDependentsResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    confirmation_digest: Annotated[str, Field(title='Confirmation Digest')]
    connection_id: Annotated[str, Field(title='Connection Id')]
    orphaned_dependents: Annotated[
        list[CrmRemovalOrphan], Field(title='Orphaned Dependents')
    ]
    runs: Annotated[list[CrmRemovalRun], Field(title='Runs')]


class DataFrameResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    columns: Annotated[list[str], Field(title='Columns')]
    rows: Annotated[list[dict[str, JsonValue | None]], Field(title='Rows')]


class DuplicateExecutionExceptionPageResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    aggregates: DuplicateExecutionExceptionAggregates
    exceptions: Annotated[
        list[DuplicateExecutionExceptionRow], Field(title='Exceptions')
    ]
    next_cursor: Annotated[str | None, Field(title='Next Cursor')] = None
    outcome_generation: Annotated[int, Field(ge=0, title='Outcome Generation')]
    plan_digest: Annotated[str, Field(title='Plan Digest')]
    run_id: Annotated[str, Field(min_length=1, title='Run Id')]


class DuplicateResolutionCatalogEntry(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    product_key: Annotated[
        Literal['easyimports.duplicate_resolution'], Field(title='Product Key')
    ]
    tracks: DuplicateResolutionTrackCatalog
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class DuplicateResolutionCreate(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    analysis_as_of_date: Annotated[date, Field(title='Analysis As Of Date')]
    entity: Annotated[Literal['account', 'person'], Field(title='Entity')]
    matching_mode: Annotated[
        Literal['default', 'exact_only'] | None, Field(title='Matching Mode')
    ] = 'default'
    maximum_modes: DuplicateResolutionMaximumModes | None = None
    product_key: Annotated[
        Literal['easyimports.duplicate_resolution'], Field(title='Product Key')
    ]
    target_provider_id: Annotated[
        str, Field(max_length=128, min_length=1, title='Target Provider Id')
    ]
    uploads: DuplicateResolutionUploads


class ErrorBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    code: Annotated[str, Field(title='Code')]
    details: JsonValue | None = None
    error_id: Annotated[str | None, Field(title='Error Id')] = None
    message: Annotated[str, Field(title='Message')]


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    error: ErrorBody


class ExportArtifactResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    byte_count: Annotated[int, Field(title='Byte Count')]
    columns: Annotated[list[str], Field(title='Columns')]
    content_digest: Annotated[str, Field(title='Content Digest')]
    content_identity: Annotated[str, Field(title='Content Identity')]
    created_at: Annotated[str, Field(title='Created At')]
    download_url: Annotated[str | None, Field(title='Download Url')] = None
    export_artifact_id: Annotated[str, Field(title='Export Artifact Id')]
    filename: Annotated[str, Field(title='Filename')]
    media_type: Annotated[str, Field(title='Media Type')]
    packaging: Annotated[Literal['M2'], Field(title='Packaging')]
    profile_contract_digest: Annotated[str, Field(title='Profile Contract Digest')]
    profile_key: Annotated[str, Field(title='Profile Key')]
    row_count: Annotated[int, Field(title='Row Count')]
    serializer_contract_digest: Annotated[
        str, Field(title='Serializer Contract Digest')
    ]
    source_run_id: Annotated[str, Field(title='Source Run Id')]
    source_run_revision: Annotated[int, Field(title='Source Run Revision')]
    source_snapshot_digest: Annotated[str, Field(title='Source Snapshot Digest')]
    table: ExportArtifactTableResource


class ExportArtifactsCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    profile_key: Annotated[ProfileKey | None, Field(title='Profile Key')] = None
    tables: Annotated[list[ExportTableSelector], Field(min_length=1, title='Tables')]


class FieldMergeDecisionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    logical_field_key: Annotated[str, Field(title='Logical Field Key')]
    merge_rule: Annotated[str, Field(title='Merge Rule')]
    overwrites_existing_value: Annotated[bool, Field(title='Overwrites Existing Value')]
    source_field_key: Annotated[str | None, Field(title='Source Field Key')] = None
    source_member_id: Annotated[str | None, Field(title='Source Member Id')] = None
    value: JsonValue | None


class FieldMergePlannedSequenceResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    consolidate_pairs: Annotated[
        list[FieldMergeConsolidatePairResource], Field(title='Consolidate Pairs')
    ]
    projection_digest: Annotated[str, Field(title='Projection Digest')]
    sequence_contract: Annotated[str, Field(title='Sequence Contract')]
    sequence_digest: Annotated[str, Field(title='Sequence Digest')]
    steps: Annotated[list[FieldMergeStepResource], Field(title='Steps')]


class FieldMergePublicPlanResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    contract: Annotated[str, Field(title='Contract')]
    entity_family: Annotated[str, Field(title='Entity Family')]
    execution_intent: Annotated[str, Field(title='Execution Intent')]
    execution_intent_summary: Annotated[str, Field(title='Execution Intent Summary')]
    field_decisions: Annotated[
        list[FieldMergeDecisionResource], Field(title='Field Decisions')
    ]
    field_projection_digest: Annotated[str, Field(title='Field Projection Digest')]
    group_id: Annotated[str, Field(title='Group Id')]
    group_revision: Annotated[str, Field(title='Group Revision')]
    merge_conflicts: Annotated[
        list[dict[str, JsonValue | None]], Field(title='Merge Conflicts')
    ]
    planned_sequence: FieldMergePlannedSequenceResource
    policy_version: Annotated[str, Field(title='Policy Version')]
    populate_field_keys: Annotated[list[str], Field(title='Populate Field Keys')]
    survivor_id: Annotated[str, Field(title='Survivor Id')]


class GroupAccountabilityResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    dispositions: Annotated[list[GroupDispositionResource], Field(title='Dispositions')]


class GroupedDecisionResponse(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    action: Annotated[Literal['submit_selections'], Field(title='Action')]
    groups: Annotated[list[GroupedDecisionSelection], Field(title='Groups')]


class HTTPValidationError(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    detail: Annotated[list[ValidationError] | None, Field(title='Detail')] = None


class ListDuplicateDecisionBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    actions: Annotated[list[str], Field(title='Actions')]
    audit_df: DataFrameResource
    columns: Annotated[list[DecisionColumnResource], Field(title='Columns')]
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[Literal['list_duplicates'], Field(title='Decision Type')]
    group_id_col: Annotated[str | None, Field(title='Group Id Col')]
    message: Annotated[str, Field(title='Message')]
    option_id_col: Annotated[str, Field(title='Option Id Col')]
    resolved_group_ids: Annotated[list[str], Field(title='Resolved Group Ids')]
    rows_df: DataFrameResource
    status: Annotated[str, Field(title='Status')]
    title: Annotated[str, Field(title='Title')]


class ListDuplicateDecisionCommand(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_id: Annotated[str, Field(min_length=1, title='Decision Id')]
    decision_type: Annotated[Literal['list_duplicates'], Field(title='Decision Type')]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    response: GroupedDecisionResponse


class ListDuplicateDecisionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    body: ListDuplicateDecisionBody
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[Literal['list_duplicates'], Field(title='Decision Type')]
    phase_id: Annotated[str, Field(title='Phase Id')]


class ListImportCatalogEntry(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    product_key: Annotated[
        Literal['easyimports.list_import'], Field(title='Product Key')
    ]
    tracks: ListImportTrackCatalog
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class ListImportCreate(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    campaign_member_policy: CampaignMemberPolicyResource | None = None
    column_mapping: ColumnMappingWorkflowBind
    connection_id: Annotated[ConnectionId2 | None, Field(title='Connection Id')] = None
    duplicate_analysis_as_of_date: Annotated[
        date | None, Field(title='Duplicate Analysis As Of Date')
    ] = None
    maximum_modes: ListImportMaximumModes | None = None
    options: ListImportOptions | None = None
    product_key: Annotated[
        Literal['easyimports.list_import'], Field(title='Product Key')
    ]
    target_provider_id: Annotated[
        str, Field(max_length=128, min_length=1, title='Target Provider Id')
    ]
    update_existing_contact_information: Annotated[
        bool | None, Field(title='Update Existing Contact Information')
    ] = False
    uploads: ListImportUploads


class ListImportTerminalEvidence(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    accountability: RowAccountabilityResource
    delivery_manifest: DeliveryManifestResource | None
    receipts: Annotated[list[TrackReceiptResource], Field(title='Receipts')]
    workflow_key: Annotated[
        Literal['easyimports.list_import'], Field(title='Workflow Key')
    ]
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class LocalStorageStatusResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    application_root: Annotated[str, Field(title='Application Root')]
    bootstrap_path: Annotated[str, Field(title='Bootstrap Path')]
    can_change_location: Annotated[bool, Field(title='Can Change Location')]
    change_blocked_reason: Annotated[
        str | None, Field(title='Change Blocked Reason')
    ] = None
    data_root: Annotated[str, Field(title='Data Root')]
    is_default_data_root: Annotated[bool, Field(title='Is Default Data Root')]
    message: Annotated[str | None, Field(title='Message')] = None
    pending_data_root: Annotated[str | None, Field(title='Pending Data Root')] = None
    restart_required: Annotated[bool | None, Field(title='Restart Required')] = None
    source: Annotated[str, Field(title='Source')]
    vault_health: SecretVaultHealthResource


class PersonDuplicateReviewBodyResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    advanced_review_required: Annotated[bool, Field(title='Advanced Review Required')]
    allowed_actions: Annotated[list[str], Field(title='Allowed Actions')]
    confidence_band: Annotated[str, Field(title='Confidence Band')]
    confidence_score: Annotated[int, Field(title='Confidence Score')]
    conflicts_df: DataFrameResource
    duplicate_group_id: Annotated[str, Field(title='Duplicate Group Id')]
    edge_decisions_df: DataFrameResource
    evidence_df: DataFrameResource
    execution_blockers: Annotated[list[str], Field(title='Execution Blockers')]
    field_merge_execution_intent: Annotated[
        str, Field(title='Field Merge Execution Intent')
    ]
    field_merge_execution_intent_summary: Annotated[
        str, Field(title='Field Merge Execution Intent Summary')
    ]
    field_merge_plan: FieldMergePublicPlanResource
    field_projection_digest: Annotated[str, Field(title='Field Projection Digest')]
    field_recommendations_df: DataFrameResource
    group_df: DataFrameResource
    group_members_df: DataFrameResource
    group_revision: Annotated[str, Field(title='Group Revision')]
    group_status: Annotated[str, Field(title='Group Status')]
    merge_conflicts_df: DataFrameResource
    message: Annotated[str, Field(title='Message')]
    object_type: Annotated[str, Field(title='Object Type')]
    people_df: DataFrameResource
    projected_survivor_df: DataFrameResource
    quarantined_people_df: DataFrameResource
    ranking_df: DataFrameResource
    recommended_survivor_df: DataFrameResource
    recommended_survivor_id: Annotated[str, Field(title='Recommended Survivor Id')]
    recommended_survivor_type: Annotated[str, Field(title='Recommended Survivor Type')]
    review_lane: Annotated[str, Field(title='Review Lane')]
    revision_df: DataFrameResource
    selected_survivor_df: DataFrameResource
    selected_survivor_id: Annotated[str, Field(title='Selected Survivor Id')]
    selected_survivor_type: Annotated[str, Field(title='Selected Survivor Type')]
    title: Annotated[str, Field(title='Title')]


class PersonReviewDecisionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    body: PersonDuplicateReviewBodyResource
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['person_duplicate_group_review'], Field(title='Decision Type')
    ]
    phase_id: Annotated[str, Field(title='Phase Id')]


class ReviewWindowGroupResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    advanced_review_required: Annotated[bool, Field(title='Advanced Review Required')]
    allowed_actions: Annotated[
        list[Literal['approve', 'override_survivor', 'decline', 'quarantine']],
        Field(title='Allowed Actions'),
    ]
    confidence_band: Annotated[str, Field(min_length=1, title='Confidence Band')]
    confidence_score: Annotated[int, Field(ge=0, title='Confidence Score')]
    conflicts: Annotated[
        list[dict[str, JsonValue | None]] | None, Field(title='Conflicts')
    ] = None
    entity_family: Annotated[Literal['company', 'person'], Field(title='Entity Family')]
    evidence: Annotated[
        list[dict[str, JsonValue | None]] | None, Field(title='Evidence')
    ] = None
    execution_blockers: Annotated[list[str], Field(title='Execution Blockers')]
    field_merge_plan: FieldMergePublicPlanResource | None = None
    group_id: Annotated[str, Field(min_length=1, title='Group Id')]
    group_revision: Annotated[str, Field(min_length=1, title='Group Revision')]
    group_status: Annotated[str, Field(min_length=1, title='Group Status')]
    members: Annotated[list[ReviewWindowMemberResource], Field(title='Members')]
    recommended_survivor_id: Annotated[
        str | None, Field(title='Recommended Survivor Id')
    ] = None
    review_lane: Annotated[str, Field(min_length=1, title='Review Lane')]
    selected_survivor_id: Annotated[str | None, Field(title='Selected Survivor Id')] = (
        None
    )


class ReviewWindowResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decided_group_count: Annotated[int, Field(ge=0, title='Decided Group Count')]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    group_end: Annotated[int, Field(ge=1, title='Group End')]
    group_start: Annotated[int, Field(ge=1, title='Group Start')]
    groups: Annotated[
        list[ReviewWindowGroupResource], Field(min_length=1, title='Groups')
    ]
    next_cursor: Annotated[str | None, Field(title='Next Cursor')] = None
    outcome: Annotated[Literal['next_window'], Field(title='Outcome')] = 'next_window'
    page_size: Annotated[Literal[5], Field(title='Page Size')] = 5
    remaining_group_count: Annotated[int, Field(ge=1, title='Remaining Group Count')]
    review_contract: Annotated[
        Literal['easyimports.crm.duplicate_review_window.v1'],
        Field(title='Review Contract'),
    ]
    total_group_count: Annotated[int, Field(ge=1, title='Total Group Count')]
    window_digest: Annotated[str, Field(min_length=1, title='Window Digest')]
    window_id: Annotated[str, Field(min_length=1, title='Window Id')]


class ReviewWindowSubmitResultBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    review_window: Annotated[
        ReviewWindowResource | ReviewCompleteResource | ReviewWindowNotReadyResource,
        Field(title='Review Window'),
    ]


class ReviewedGroupDispositionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    advanced_review_required: Annotated[
        bool | None, Field(title='Advanced Review Required')
    ] = False
    allowed_actions: Annotated[list[str] | None, Field(title='Allowed Actions')] = None
    decided_at: Annotated[str | None, Field(title='Decided At')] = None
    decided_by: Annotated[str | None, Field(title='Decided By')] = None
    decision_origin: Annotated[
        Literal['operator', 'high_confidence_threshold'] | None,
        Field(title='Decision Origin'),
    ] = None
    disposition: Annotated[
        Literal['approved', 'declined', 'quarantined'], Field(title='Disposition')
    ]
    group_evidence_digest: Annotated[
        str | None, Field(title='Group Evidence Digest')
    ] = None
    group_id: Annotated[str, Field(min_length=1, title='Group Id')]
    group_revision: Annotated[str, Field(min_length=1, title='Group Revision')]
    group_status: Annotated[str | None, Field(title='Group Status')] = 'reviewable'
    loser_ids: Annotated[list[str] | None, Field(title='Loser Ids')] = None
    member_ids: Annotated[list[str] | None, Field(title='Member Ids')] = None
    members: Annotated[
        list[ReviewedGroupMemberResource] | None, Field(title='Members')
    ] = None
    recommended_survivor_id: Annotated[
        str | None, Field(title='Recommended Survivor Id')
    ] = None
    selected_survivor_id: Annotated[str | None, Field(title='Selected Survivor Id')] = (
        None
    )


class ReviewedResultResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    analysis_work_digest: Annotated[str | None, Field(title='Analysis Work Digest')] = (
        None
    )
    analyzed_record_count: Annotated[int, Field(ge=0, title='Analyzed Record Count')]
    approved_merge_group_count: Annotated[
        int, Field(ge=0, title='Approved Merge Group Count')
    ]
    auto_approved_group_count: Annotated[
        int | None, Field(ge=0, title='Auto Approved Group Count')
    ] = 0
    complete: Annotated[bool, Field(title='Complete')]
    decided_group_count: Annotated[int, Field(ge=0, title='Decided Group Count')]
    decision_set_content_digest: Annotated[
        str, Field(min_length=1, title='Decision Set Content Digest')
    ]
    declined_group_count: Annotated[int, Field(ge=0, title='Declined Group Count')]
    dispositions: Annotated[
        list[ReviewedGroupDispositionResource], Field(title='Dispositions')
    ]
    entity: Annotated[Literal['account', 'person'], Field(title='Entity')]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    loser_count: Annotated[int, Field(ge=0, title='Loser Count')]
    mapping_digest: Annotated[str | None, Field(title='Mapping Digest')] = None
    merge_plan_frozen: Annotated[bool | None, Field(title='Merge Plan Frozen')] = False
    quarantined_group_count: Annotated[
        int, Field(ge=0, title='Quarantined Group Count')
    ]
    review_binding_digest: Annotated[
        str | None, Field(title='Review Binding Digest')
    ] = None
    review_contract: Annotated[
        Literal[
            'easyimports.crm.duplicate_reviewed_result.v1',
            'easyimports.crm.duplicate_reviewed_result.v2',
        ],
        Field(title='Review Contract'),
    ]
    review_handoff_id: Annotated[str | None, Field(title='Review Handoff Id')] = None
    review_run_id: Annotated[str, Field(min_length=1, title='Review Run Id')]
    source_run_id: Annotated[str | None, Field(title='Source Run Id')] = None
    source_snapshot_digest: Annotated[
        str | None, Field(title='Source Snapshot Digest')
    ] = None
    survivor_count: Annotated[int, Field(ge=0, title='Survivor Count')]
    total_group_count: Annotated[int, Field(ge=0, title='Total Group Count')]


class SingleDatasetCatalogEntry(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    product_key: Annotated[
        Literal['easyimports.single_dataset_import'], Field(title='Product Key')
    ]
    tracks: SingleDatasetTrackCatalog
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class SingleDatasetImportCreate(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    canon_profile: Annotated[
        Literal['new_list', 'accounts', 'contacts', 'leads'] | None,
        Field(title='Canon Profile'),
    ] = None
    column_mapping: ColumnMappingWorkflowBind
    content_type: Annotated[
        Literal['accounts', 'people', 'people_and_accounts'] | None,
        Field(title='Content Type'),
    ] = None
    maximum_modes: SingleDatasetMaximumModes | None = None
    options: SingleDatasetImportOptions | None = None
    person_kind: Annotated[
        Literal['contact', 'lead', 'mixed', 'generic'] | None,
        Field(title='Person Kind'),
    ] = None
    product_key: Annotated[
        Literal['easyimports.single_dataset_import'], Field(title='Product Key')
    ]
    target_object: Annotated[
        Literal['account', 'contact', 'lead'], Field(title='Target Object')
    ]
    target_provider_id: Annotated[
        str, Field(max_length=128, min_length=1, title='Target Provider Id')
    ]
    uploads: SingleDatasetUploads


class SingleDatasetTerminalEvidence(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    accountability: RowAccountabilityResource
    delivery_manifest: DeliveryManifestResource | None
    receipts: Annotated[list[TrackReceiptResource], Field(title='Receipts')]
    workflow_key: Annotated[
        Literal['easyimports.single_dataset_import'], Field(title='Workflow Key')
    ]
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class TargetCatalog(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    targets: Annotated[list[TargetResource], Field(title='Targets')]


class UploadCollection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    uploads: Annotated[list[UploadResource], Field(title='Uploads')]


class UploadedAccountIdDisagreementBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    actions: Annotated[list[str], Field(title='Actions')]
    audit_df: DataFrameResource
    columns: Annotated[list[DecisionColumnResource], Field(title='Columns')]
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['uploaded_account_id_disagreement'], Field(title='Decision Type')
    ]
    group_id_col: Annotated[str | None, Field(title='Group Id Col')]
    message: Annotated[str, Field(title='Message')]
    option_id_col: Annotated[str, Field(title='Option Id Col')]
    resolved_group_ids: Annotated[list[str], Field(title='Resolved Group Ids')]
    rows_df: DataFrameResource
    status: Annotated[str, Field(title='Status')]
    title: Annotated[str, Field(title='Title')]


class UploadedAccountIdDisagreementCommand(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_id: Annotated[str, Field(min_length=1, title='Decision Id')]
    decision_type: Annotated[
        Literal['uploaded_account_id_disagreement'], Field(title='Decision Type')
    ]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    response: GroupedDecisionResponse


class UploadedAccountIdDisagreementResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    body: UploadedAccountIdDisagreementBody
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['uploaded_account_id_disagreement'], Field(title='Decision Type')
    ]
    phase_id: Annotated[str, Field(title='Phase Id')]


class UploadedPersonIdDisagreementBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    actions: Annotated[list[str], Field(title='Actions')]
    audit_df: DataFrameResource
    columns: Annotated[list[DecisionColumnResource], Field(title='Columns')]
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['uploaded_person_id_disagreement'], Field(title='Decision Type')
    ]
    group_id_col: Annotated[str | None, Field(title='Group Id Col')]
    message: Annotated[str, Field(title='Message')]
    option_id_col: Annotated[str, Field(title='Option Id Col')]
    resolved_group_ids: Annotated[list[str], Field(title='Resolved Group Ids')]
    rows_df: DataFrameResource
    status: Annotated[str, Field(title='Status')]
    title: Annotated[str, Field(title='Title')]


class UploadedPersonIdDisagreementCommand(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_id: Annotated[str, Field(min_length=1, title='Decision Id')]
    decision_type: Annotated[
        Literal['uploaded_person_id_disagreement'], Field(title='Decision Type')
    ]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    response: GroupedDecisionResponse


class UploadedPersonIdDisagreementResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    body: UploadedPersonIdDisagreementBody
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['uploaded_person_id_disagreement'], Field(title='Decision Type')
    ]
    phase_id: Annotated[str, Field(title='Phase Id')]


class ValidationDecisionBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    actions: Annotated[list[str], Field(title='Actions')]
    audit_df: DataFrameResource
    columns: Annotated[list[DecisionColumnResource], Field(title='Columns')]
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[Literal['validation_failed'], Field(title='Decision Type')]
    group_id_col: Annotated[str | None, Field(title='Group Id Col')]
    message: Annotated[str, Field(title='Message')]
    option_id_col: Annotated[str, Field(title='Option Id Col')]
    resolved_group_ids: Annotated[list[str], Field(title='Resolved Group Ids')]
    rows_df: DataFrameResource
    status: Annotated[str, Field(title='Status')]
    title: Annotated[str, Field(title='Title')]


class ValidationDecisionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    body: ValidationDecisionBody
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[Literal['validation_failed'], Field(title='Decision Type')]
    phase_id: Annotated[str, Field(title='Phase Id')]


class ValidationDecisionResponse(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    action: Annotated[
        Literal['submit_edits', 'exclude_remaining_invalid_rows'], Field(title='Action')
    ]
    rows: Annotated[list[ValidationDecisionRow] | None, Field(title='Rows')] = None


class AccountDuplicateReviewBodyResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    accounts_df: DataFrameResource
    advanced_review_required: Annotated[bool, Field(title='Advanced Review Required')]
    allowed_actions: Annotated[list[str], Field(title='Allowed Actions')]
    confidence_band: Annotated[str, Field(title='Confidence Band')]
    confidence_score: Annotated[int, Field(title='Confidence Score')]
    conflicts_df: DataFrameResource
    duplicate_group_id: Annotated[str, Field(title='Duplicate Group Id')]
    edge_decisions_df: DataFrameResource
    evidence_df: DataFrameResource
    field_merge_execution_intent: Annotated[
        str, Field(title='Field Merge Execution Intent')
    ]
    field_merge_execution_intent_summary: Annotated[
        str, Field(title='Field Merge Execution Intent Summary')
    ]
    field_merge_plan: FieldMergePublicPlanResource
    field_projection_digest: Annotated[str, Field(title='Field Projection Digest')]
    field_recommendations_df: DataFrameResource
    group_df: DataFrameResource
    group_members_df: DataFrameResource
    group_revision: Annotated[str, Field(title='Group Revision')]
    merge_conflicts_df: DataFrameResource
    message: Annotated[str, Field(title='Message')]
    object_type: Annotated[str, Field(title='Object Type')]
    projected_survivor_df: DataFrameResource
    ranking_df: DataFrameResource
    recommended_survivor_df: DataFrameResource
    recommended_survivor_id: Annotated[str, Field(title='Recommended Survivor Id')]
    review_lane: Annotated[str, Field(title='Review Lane')]
    revision_df: DataFrameResource
    selected_survivor_df: DataFrameResource
    selected_survivor_id: Annotated[str, Field(title='Selected Survivor Id')]
    title: Annotated[str, Field(title='Title')]


class AccountReviewDecisionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    body: AccountDuplicateReviewBodyResource
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['account_duplicate_group_review'], Field(title='Decision Type')
    ]
    phase_id: Annotated[str, Field(title='Phase Id')]


class ColumnMappingChoiceCollection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    choices: Annotated[list[ColumnMappingChoiceResource], Field(title='Choices')]


class CrmAccountMatchDecisionBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    actions: Annotated[list[str], Field(title='Actions')]
    audit_df: DataFrameResource
    columns: Annotated[list[DecisionColumnResource], Field(title='Columns')]
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['multiple_crm_account_matches'], Field(title='Decision Type')
    ]
    group_id_col: Annotated[str | None, Field(title='Group Id Col')]
    message: Annotated[str, Field(title='Message')]
    option_id_col: Annotated[str, Field(title='Option Id Col')]
    resolved_group_ids: Annotated[list[str], Field(title='Resolved Group Ids')]
    rows_df: DataFrameResource
    status: Annotated[str, Field(title='Status')]
    title: Annotated[str, Field(title='Title')]


class CrmAccountMatchDecisionCommand(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_id: Annotated[str, Field(min_length=1, title='Decision Id')]
    decision_type: Annotated[
        Literal['multiple_crm_account_matches'], Field(title='Decision Type')
    ]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    response: GroupedDecisionResponse


class CrmAccountMatchDecisionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    body: CrmAccountMatchDecisionBody
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['multiple_crm_account_matches'], Field(title='Decision Type')
    ]
    phase_id: Annotated[str, Field(title='Phase Id')]


class CrmConnectionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    authorization: CrmAuthorizationDescriptor | None = None
    capabilities: Annotated[
        dict[str, CrmEntityCapabilityResource], Field(title='Capabilities')
    ]
    capability_profile_digest: Annotated[
        str | None, Field(title='Capability Profile Digest')
    ] = None
    connection_id: Annotated[str, Field(title='Connection Id')]
    display_label: Annotated[str | None, Field(title='Display Label')] = None
    execution_target_provider_id: Annotated[
        str | None, Field(title='Execution Target Provider Id')
    ] = None
    last_reclaim: CrmConnectionLastReclaim | None = None
    maximum_authorization: CrmMaximumAuthorizationResource
    provider_key: Annotated[
        Literal['fake', 'salesforce', 'hubspot'], Field(title='Provider Key')
    ]
    provider_label: Annotated[str, Field(title='Provider Label')]
    reconnect_attempt_id: Annotated[str | None, Field(title='Reconnect Attempt Id')] = (
        None
    )
    reconnect_epoch: Annotated[int | None, Field(title='Reconnect Epoch')] = None
    status: Annotated[
        Literal[
            'pending',
            'connected',
            'credential_unavailable',
            'disconnected',
            'revoked',
            'failed',
        ],
        Field(title='Status'),
    ]
    supported_entity_families: Annotated[
        list[Literal['company', 'person']], Field(title='Supported Entity Families')
    ]
    supported_source_modes: Annotated[
        list[
            Literal[
                'candidate_upload', 'acquire_all', 'selected_ids', 'uploaded_population'
            ]
        ],
        Field(title='Supported Source Modes'),
    ]


class CrmPersonMatchDecisionBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    actions: Annotated[list[str], Field(title='Actions')]
    audit_df: DataFrameResource
    columns: Annotated[list[DecisionColumnResource], Field(title='Columns')]
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['multiple_crm_matches'], Field(title='Decision Type')
    ]
    group_id_col: Annotated[str | None, Field(title='Group Id Col')]
    message: Annotated[str, Field(title='Message')]
    option_id_col: Annotated[str, Field(title='Option Id Col')]
    resolved_group_ids: Annotated[list[str], Field(title='Resolved Group Ids')]
    rows_df: DataFrameResource
    status: Annotated[str, Field(title='Status')]
    title: Annotated[str, Field(title='Title')]


class CrmPersonMatchDecisionCommand(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_id: Annotated[str, Field(min_length=1, title='Decision Id')]
    decision_type: Annotated[
        Literal['multiple_crm_matches'], Field(title='Decision Type')
    ]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    response: GroupedDecisionResponse


class CrmPersonMatchDecisionResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    body: CrmPersonMatchDecisionBody
    decision_id: Annotated[str, Field(title='Decision Id')]
    decision_type: Annotated[
        Literal['multiple_crm_matches'], Field(title='Decision Type')
    ]
    phase_id: Annotated[str, Field(title='Phase Id')]


class DuplicateResolutionTerminalEvidence(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    accountability: GroupAccountabilityResource
    delivery_manifest: DeliveryManifestResource | None
    entity: Annotated[Literal['account', 'person'], Field(title='Entity')]
    oversized_quarantine_components: Annotated[
        list[OversizedQuarantineComponentResource] | None,
        Field(title='Oversized Quarantine Components'),
    ] = None
    receipts: Annotated[list[TrackReceiptResource], Field(title='Receipts')]
    workflow_key: Annotated[
        Literal['easyimports.duplicate_resolution'], Field(title='Workflow Key')
    ]
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class ExportArtifactCollection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    export_artifacts: Annotated[
        list[ExportArtifactResource], Field(title='Export Artifacts')
    ]


class InvalidateMergePlanFreezeResultBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    invalidated: Annotated[Literal[True], Field(title='Invalidated')] = True
    reviewed_result: ReviewedResultResource
    revoked_continuation_run_ids: Annotated[
        list[str] | None, Field(title='Revoked Continuation Run Ids')
    ] = None
    source_run_id: Annotated[str | None, Field(title='Source Run Id')] = None


class MergePlanHandoffResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    continuation_revision: Annotated[int, Field(ge=0, title='Continuation Revision')]
    continuation_run_id: Annotated[
        str, Field(min_length=1, title='Continuation Run Id')
    ]
    decision_set_handoff: DecisionSetHandoffResource
    effect_intent: EffectIntentResource | None = None
    handoff_contract: Annotated[
        Literal['easyimports.crm.duplicate_merge_plan_handoff.v1'],
        Field(title='Handoff Contract'),
    ]
    maximum_mode: Annotated[
        Literal['disabled', 'preview', 'dry_run', 'execute'] | None,
        Field(title='Maximum Mode'),
    ] = None
    plan_content_digest: Annotated[str | None, Field(title='Plan Content Digest')] = (
        None
    )
    reviewed_result: ReviewedResultResource
    source_read_grant_authorizes_writes: Annotated[
        Literal[False], Field(title='Source Read Grant Authorizes Writes')
    ] = False
    supported_modes: Annotated[
        list[Literal['disabled', 'preview', 'dry_run', 'execute']] | None,
        Field(title='Supported Modes'),
    ] = None
    track: Annotated[
        Literal[
            'reference_acquisition',
            'account_provisioning',
            'account_writes',
            'person_duplicate_resolution',
            'people_writes',
            'campaign_member_writes',
            'dataset_writes',
            'duplicate_execution',
            'delivery',
            'crm_query',
        ]
        | None,
        Field(title='Track'),
    ] = None
    workflow_status: Annotated[str, Field(min_length=1, title='Workflow Status')]
    write_authorization_required: Annotated[
        Literal[True], Field(title='Write Authorization Required')
    ] = True


class Products(
    RootModel[
        ListImportCatalogEntry
        | AccountListCatalogEntry
        | SingleDatasetCatalogEntry
        | DuplicateResolutionCatalogEntry
    ]
):
    root: Annotated[
        ListImportCatalogEntry
        | AccountListCatalogEntry
        | SingleDatasetCatalogEntry
        | DuplicateResolutionCatalogEntry,
        Field(discriminator='product_key'),
    ]


class ProductCatalog(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    products: Annotated[list[Products], Field(title='Products')]


class ReviewWindowSubmitResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    command_id: Annotated[str, Field(min_length=1, title='Command Id')]
    command_kind: Annotated[
        Literal['submit_duplicate_review_window', 'submit_duplicate_review_end_early'],
        Field(title='Command Kind'),
    ]
    error_code: Annotated[str | None, Field(title='Error Code')] = None
    message: Annotated[str | None, Field(title='Message')] = None
    outcome: Annotated[Literal['accepted'], Field(title='Outcome')] = 'accepted'
    resource: Annotated[str, Field(min_length=1, title='Resource')]
    result: ReviewWindowSubmitResultBody
    revision: Annotated[int, Field(ge=1, title='Revision')]
    run_id: Annotated[str, Field(min_length=1, title='Run Id')]
    stage: Annotated[str, Field(min_length=1, title='Stage')]
    workflow_status: Annotated[str, Field(min_length=1, title='Workflow Status')]


class ReviewedDispositionWindowResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_set_content_digest: Annotated[
        str, Field(min_length=1, title='Decision Set Content Digest')
    ]
    dispositions: Annotated[
        list[ReviewedGroupDispositionResource],
        Field(max_length=25, title='Dispositions'),
    ]
    expected_revision: Annotated[int, Field(ge=0, title='Expected Revision')]
    next_cursor: Annotated[str | None, Field(title='Next Cursor')]
    previous_cursor: Annotated[str | None, Field(title='Previous Cursor')]
    review_run_id: Annotated[str, Field(min_length=1, title='Review Run Id')]
    reviewed_result_content_digest: Annotated[
        str, Field(min_length=1, title='Reviewed Result Content Digest')
    ]
    total_group_count: Annotated[int, Field(ge=0, title='Total Group Count')]
    window_contract: Annotated[
        Literal['easyimports.crm.duplicate_reviewed_disposition_window.v1'],
        Field(title='Window Contract'),
    ]
    window_end: Annotated[int, Field(ge=0, title='Window End')]
    window_start: Annotated[int, Field(ge=0, title='Window Start')]
    window_token: Annotated[str, Field(min_length=1, title='Window Token')]


class ValidationDecisionCommand(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    decision_id: Annotated[str, Field(min_length=1, title='Decision Id')]
    decision_type: Annotated[Literal['validation_failed'], Field(title='Decision Type')]
    expected_revision: Annotated[int, Field(ge=1, title='Expected Revision')]
    response: ValidationDecisionResponse


class WorkflowResource(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    continuation_available: Annotated[
        bool | None, Field(title='Continuation Available')
    ] = False
    continuation_generation: Annotated[
        int | None, Field(title='Continuation Generation')
    ] = 0
    decision: Annotated[
        ValidationDecisionResource
        | ListDuplicateDecisionResource
        | CrmPersonMatchDecisionResource
        | CrmAccountMatchDecisionResource
        | UploadedAccountIdDisagreementResource
        | UploadedPersonIdDisagreementResource
        | AccountReviewDecisionResource
        | PersonReviewDecisionResource
        | None,
        Field(title='Decision'),
    ]
    duplicate_analysis_progress: DuplicateAnalysisProgress | None = None
    duplicate_execution_exception_page: (
        DuplicateExecutionExceptionPageResource | None
    ) = None
    effect_grants: Annotated[
        list[EffectAuthorizationGrantResource], Field(title='Effect Grants')
    ]
    effect_intent: EffectIntentResource | None
    effect_review: EffectReviewResource | None = None
    error: WorkflowFailureResource | None
    links: Annotated[dict[str, str], Field(title='Links')]
    outcome_generation: Annotated[int | None, Field(title='Outcome Generation')] = 0
    oversized_quarantine_components: Annotated[
        list[OversizedQuarantineComponentResource] | None,
        Field(title='Oversized Quarantine Components'),
    ] = None
    paused_effect_diagnostic: PausedEffectDiagnostic | None = None
    reference_acquisition_progress: ReferenceAcquisitionProgress | None = None
    remote_outcome: Annotated[
        Literal['none', 'unknown'] | None, Field(title='Remote Outcome')
    ] = None
    review_handoff: ReviewHandoffResource | None
    review_progress: DuplicateReviewProgressResource | None = None
    revision: Annotated[int, Field(title='Revision')]
    run_id: Annotated[str, Field(title='Run Id')]
    stage: Annotated[str, Field(title='Stage')]
    status: Annotated[
        Literal[
            'ready',
            'running',
            'needs_decision',
            'awaiting_effect_authorization',
            'awaiting_review',
            'awaiting_execution_continuation',
            'paused_unknown',
            'paused_verification',
            'succeeded',
            'failed',
        ],
        Field(title='Status'),
    ]
    summary: Annotated[dict[str, int], Field(title='Summary')]
    target_provider_id: Annotated[str | None, Field(title='Target Provider Id')]
    terminal_evidence: Annotated[
        ListImportTerminalEvidence
        | AccountListTerminalEvidence
        | SingleDatasetTerminalEvidence
        | DuplicateResolutionTerminalEvidence
        | None,
        Field(title='Terminal Evidence'),
    ]
    workflow_key: Annotated[str, Field(title='Workflow Key')]
    workflow_version: Annotated[int, Field(title='Workflow Version')]


class CrmConnectionCollection(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    connections: Annotated[list[CrmConnectionResource], Field(title='Connections')]


class FinalizeMergePlanHandoffResultBody(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    merge_plan_handoff: MergePlanHandoffResource


class InvalidateMergePlanFreezeResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    command_id: Annotated[str, Field(min_length=1, title='Command Id')]
    command_kind: Annotated[
        Literal['invalidate_duplicate_merge_plan_freeze'], Field(title='Command Kind')
    ]
    error_code: Annotated[str | None, Field(title='Error Code')] = None
    message: Annotated[str | None, Field(title='Message')] = None
    outcome: Annotated[Literal['accepted'], Field(title='Outcome')] = 'accepted'
    resource: Annotated[str, Field(min_length=1, title='Resource')]
    result: InvalidateMergePlanFreezeResultBody
    revision: Annotated[int, Field(ge=0, title='Revision')]
    run_id: Annotated[str, Field(min_length=1, title='Run Id')]
    stage: Annotated[str, Field(min_length=1, title='Stage')]
    workflow_status: Annotated[str, Field(min_length=1, title='Workflow Status')]


class FinalizeMergePlanHandoffResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    command_id: Annotated[str, Field(min_length=1, title='Command Id')]
    command_kind: Annotated[
        Literal['finalize_duplicate_merge_plan_handoff'], Field(title='Command Kind')
    ]
    error_code: Annotated[str | None, Field(title='Error Code')] = None
    message: Annotated[str | None, Field(title='Message')] = None
    outcome: Annotated[Literal['accepted'], Field(title='Outcome')] = 'accepted'
    resource: Annotated[str, Field(min_length=1, title='Resource')]
    result: FinalizeMergePlanHandoffResultBody
    revision: Annotated[int, Field(ge=0, title='Revision')]
    run_id: Annotated[str, Field(min_length=1, title='Run Id')]
    stage: Annotated[str, Field(min_length=1, title='Stage')]
    workflow_status: Annotated[str, Field(min_length=1, title='Workflow Status')]


JsonValue.model_rebuild()
