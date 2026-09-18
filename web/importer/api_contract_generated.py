"""Generated EasyImports OpenAPI v1 constants. DO NOT EDIT."""

GENERATOR = 'datamodel-code-generator/0.48.0'
API_VERSION = '1.62.0'
OPENAPI_SHA256 = '4d3411b669f19c265beade6b8a2877da087931663e6b360ed6c1816eb209ae86'

PRODUCT_KEYS = (
    'easyimports.list_import',
    'easyimports.account_list_import',
    'easyimports.single_dataset_import',
    'easyimports.duplicate_resolution',
)
WORKFLOW_STATUSES = (
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
)
DECISION_TYPES = (
    'validation_failed',
    'list_duplicates',
    'multiple_crm_matches',
    'multiple_crm_account_matches',
    'uploaded_account_id_disagreement',
    'uploaded_person_id_disagreement',
    'account_duplicate_group_review',
    'person_duplicate_group_review',
)
AUTHORIZATION_MODES = (
    'disabled',
    'preview',
    'dry_run',
    'execute',
)
PRODUCT_TRACK_MODES = {'easyimports.list_import': {'account_provisioning': ['disabled', 'preview', 'dry_run', 'execute'], 'campaign_member_writes': ['disabled', 'preview', 'dry_run', 'execute'], 'delivery': ['disabled', 'preview', 'execute'], 'people_writes': ['disabled', 'preview', 'dry_run', 'execute'], 'person_duplicate_resolution': ['disabled', 'preview', 'dry_run', 'execute'], 'reference_acquisition': ['disabled', 'execute']}, 'easyimports.account_list_import': {'account_provisioning': ['disabled', 'preview', 'dry_run', 'execute'], 'account_writes': ['disabled'], 'delivery': ['disabled', 'preview', 'execute'], 'reference_acquisition': ['disabled', 'execute']}, 'easyimports.single_dataset_import': {'dataset_writes': ['disabled'], 'delivery': ['disabled', 'preview', 'execute']}, 'easyimports.duplicate_resolution': {'delivery': ['disabled', 'preview', 'execute'], 'duplicate_execution': ['disabled', 'preview', 'dry_run', 'execute'], 'reference_acquisition': ['disabled', 'execute']}}
