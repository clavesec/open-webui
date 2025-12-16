# OWUI Billing Integration Implementation Guide

## Overview

This guide provides step-by-step instructions for integrating OWUI with the TPAI billing system. The integration enables:

1. **JWT-based Authentication**: Users authenticate with billing-generated JWT tokens
2. **Tier-based Access Control**: FREE, PRO, and ENTERPRISE tiers with different permissions
3. **Grace Period Support**: 7-day access continuation after subscription cancellation
4. **Cross-account Architecture**: OWUI (airgapped) validates tokens from billing stack (internet-facing)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    User Payment Flow                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Shared Services Account (879381241161) - Internet-Facing       │
│                                                                 │
│  ┌──────────────────┐    ┌─────────────────────────────────┐  │
│  │  Stripe Webhooks │───▶│  Billing Lambda (webhook)       │  │
│  └──────────────────┘    │  - Creates customer record      │  │
│                          │  - HMAC-SHA256 hashes email     │  │
│                          └──────────┬──────────────────────┘  │
│                                     │                          │
│                          ┌──────────▼──────────────────────┐  │
│                          │  DynamoDB (customers table)     │  │
│                          │  - Pseudonymous records         │  │
│                          └──────────┬──────────────────────┘  │
│                                     │                          │
│  ┌────────────────────┐  ┌──────────▼──────────────────────┐  │
│  │ User requests      │─▶│  Access Token Lambda            │  │
│  │ token via email    │  │  - Queries DynamoDB             │  │
│  └────────────────────┘  │  - Generates signed JWT (RS256) │  │
│                          │  - 24-hour expiration           │  │
│                          └──────────┬──────────────────────┘  │
└─────────────────────────────────────┼───────────────────────────┘
                                      │
                          JWT Token with:
                          - customer_id (UUID)
                          - subscription_tier
                          - subscription_status
                          - exp (24h)
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ Product Account (405894864934) - Airgapped                     │
│                                                                 │
│  ┌──────────────────┐    ┌─────────────────────────────────┐  │
│  │ User presents    │───▶│  OWUI Backend (FastAPI)         │  │
│  │ JWT token        │    │  - Validates JWT signature      │  │
│  └──────────────────┘    │  - Extracts customer_id         │  │
│                          │  - NO database query to billing │  │
│                          └──────────┬──────────────────────┘  │
│                                     │                          │
│                          ┌──────────▼──────────────────────┐  │
│                          │  OWUI Database (PostgreSQL/RDS) │  │
│                          │  - Creates/updates user         │  │
│                          │  - Links billing_customer_id    │  │
│                          │  - Enforces tier permissions    │  │
│                          └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Implementation Steps

### Step 1: Add billing_customer_id to User Schema

**File**: `Product/owui/backend/open_webui/models/users.py`

#### 1.1: Update SQLAlchemy User Model

Add the new column after line 42 (after oauth_sub):

```python
# Billing integration field
# Links OWUI user to billing system customer record (UUID from billing DynamoDB)
# This enables cross-account subscription management without direct database access
billing_customer_id = Column(String, nullable=True, index=True)
```

#### 1.2: Update Pydantic UserModel

Add the field after line 91 (after oauth_sub):

```python
# Billing integration
billing_customer_id: Optional[str] = None
```

#### 1.3: Create Database Migration

Create new migration file: `Product/owui/backend/open_webui/migrations/versions/XXXX_add_billing_customer_id.py`

```python
"""Add billing_customer_id to user table

Revision ID: XXXX_add_billing_customer_id
Revises: <previous_revision>
Create Date: 2025-11-24

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'XXXX_add_billing_customer_id'
down_revision = '<get from latest migration>'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add billing_customer_id column to user table
    op.add_column('user', sa.Column('billing_customer_id', sa.String(), nullable=True))

    # Add index for faster lookups
    op.create_index(
        op.f('ix_user_billing_customer_id'),
        'user',
        ['billing_customer_id'],
        unique=False
    )


def downgrade() -> None:
    # Remove index
    op.drop_index(op.f('ix_user_billing_customer_id'), table_name='user')

    # Remove column
    op.drop_column('user', 'billing_customer_id')
```

**Get Latest Revision:**

```bash
cd Product/owui/backend
# Find the latest migration revision
ls -lt open_webui/migrations/versions/ | head -2
```

### Step 2: Add Billing JWT Validation

**File**: `Product/owui/backend/open_webui/utils/auth.py`

#### 2.1: Add Environment Variable

Add to `Product/owui/backend/open_webui/env.py` (around line 27):

```python
# Billing Integration
BILLING_JWT_PUBLIC_KEY = os.getenv("BILLING_JWT_PUBLIC_KEY", "")
BILLING_API_URL = os.getenv("BILLING_API_URL", "https://qdz1hzomxg.execute-api.us-east-1.amazonaws.com/")
```

#### 2.2: Add Public Key Validation Function

Add to `Product/owui/backend/open_webui/utils/auth.py` after line 137:

```python
def decode_billing_token(token: str) -> Optional[dict]:
    """
    Decodes and validates JWT token from billing system.
    Uses RS256 algorithm with public key verification.

    Args:
        token: JWT token from billing access-token endpoint

    Returns:
        dict with customer_id, subscription_tier, subscription_status, exp
        None if validation fails
    """
    try:
        if not BILLING_JWT_PUBLIC_KEY:
            log.error("BILLING_JWT_PUBLIC_KEY not configured")
            return None

        decoded = jwt.decode(
            token,
            BILLING_JWT_PUBLIC_KEY,
            algorithms=["RS256"],
            options={"verify_exp": True}
        )

        # Validate required fields
        required_fields = ["customer_id", "subscription_tier", "subscription_status"]
        for field in required_fields:
            if field not in decoded:
                log.error(f"Missing required field in billing token: {field}")
                return None

        return decoded
    except jwt.ExpiredSignatureError:
        log.warning("Billing token expired")
        return None
    except jwt.InvalidTokenError as e:
        log.error(f"Invalid billing token: {e}")
        return None
    except Exception as e:
        log.exception(f"Error decoding billing token: {e}")
        return None
```

### Step 3: Create Billing Signup Endpoint

**File**: `Product/owui/backend/open_webui/routers/auths.py`

Add new endpoint after the existing `signup` function (around line 720):

```python
class BillingSignupForm(BaseModel):
    billing_token: str
    name: str
    profile_image_url: Optional[str] = "/user.png"


@router.post("/signup/billing", response_model=SessionUserResponse)
async def signup_with_billing_token(
    request: Request,
    response: Response,
    form_data: BillingSignupForm
):
    """
    Create or update OWUI account using billing-generated JWT token.

    Flow:
    1. Validate billing JWT signature (no database query to billing system)
    2. Extract customer_id, subscription_tier, subscription_status
    3. Check if user already exists with this billing_customer_id
    4. Create new user or update existing user
    5. Generate OWUI session token
    6. Set cookie and return user data

    This is the primary entry point for paid users.
    """

    # Validate billing token
    billing_data = decode_billing_token(form_data.billing_token)
    if not billing_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired billing token"
        )

    customer_id = billing_data["customer_id"]
    subscription_tier = billing_data["subscription_tier"]
    subscription_status = billing_data["subscription_status"]

    # Check if subscription is active
    active_statuses = ["active", "trialing"]
    if subscription_status not in active_statuses:
        # Grace period might be in effect - allow access but show warning
        log.warning(
            f"User {customer_id} accessing with non-active subscription: {subscription_status}"
        )

    # Check if user already exists with this billing_customer_id
    existing_user = Users.get_user_by_billing_customer_id(customer_id)

    if existing_user:
        # Update existing user's subscription tier (in case it changed)
        user = Users.update_user_by_id(
            existing_user.id,
            {"info": {**(existing_user.info or {}), "subscription_tier": subscription_tier}}
        )
        log.info(f"Updated existing user {user.id} with tier {subscription_tier}")
    else:
        # Create new user
        # Generate unique email since we don't have user's actual email
        # (it's HMAC-hashed in billing system for PII protection)
        generated_email = f"{customer_id}@billing.tpai.local"

        # Check if user with this generated email already exists (shouldn't happen)
        if Users.get_user_by_email(generated_email):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User account conflict - contact support"
            )

        # Determine role based on tier
        role = "user"  # Default role for all paid users

        # Create user without password (billing-only authentication)
        user_id = str(uuid.uuid4())
        user = Users.insert_new_user(
            user_id,
            form_data.name,
            generated_email,
            form_data.profile_image_url,
            role,
        )

        # Link to billing customer
        Users.update_user_by_id(
            user.id,
            {
                "billing_customer_id": customer_id,
                "info": {"subscription_tier": subscription_tier}
            }
        )

        log.info(f"Created new user {user.id} with billing_customer_id {customer_id}")

    # Generate OWUI session token
    expires_delta = parse_duration(request.app.state.config.JWT_EXPIRES_IN)
    expires_at = None
    if expires_delta:
        expires_at = int(time.time()) + int(expires_delta.total_seconds())

    token = create_token(
        data={"id": user.id},
        expires_delta=expires_delta,
    )

    datetime_expires_at = (
        datetime.datetime.fromtimestamp(expires_at, datetime.timezone.utc)
        if expires_at
        else None
    )

    # Set the cookie token
    response.set_cookie(
        key="token",
        value=token,
        expires=datetime_expires_at,
        httponly=True,
        samesite=WEBUI_AUTH_COOKIE_SAME_SITE,
        secure=WEBUI_AUTH_COOKIE_SECURE,
    )

    user_permissions = get_permissions(
        user.id, request.app.state.config.USER_PERMISSIONS
    )

    return {
        "token": token,
        "token_type": "Bearer",
        "expires_at": expires_at,
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "profile_image_url": user.profile_image_url,
        "permissions": user_permissions,
    }
```

### Step 4: Add User Lookup Method

**File**: `Product/owui/backend/open_webui/models/users.py`

Add method to `UsersTable` class (around line 200):

```python
def get_user_by_billing_customer_id(self, billing_customer_id: str) -> Optional[UserModel]:
    """
    Get user by billing_customer_id (UUID from billing system).
    Used during billing token authentication to find existing user.
    """
    try:
        with get_db() as db:
            user = db.query(User).filter_by(billing_customer_id=billing_customer_id).first()
            return UserModel.model_validate(user) if user else None
    except Exception as e:
        log.exception(f"Error getting user by billing_customer_id: {e}")
        return None
```

### Step 5: Implement Tier-based Access Control

**File**: `Product/owui/backend/open_webui/utils/middleware.py`

Add tier validation middleware:

```python
def get_user_subscription_tier(user_id: str) -> str:
    """
    Get user's subscription tier from user.info.subscription_tier.

    Returns:
        "FREE" | "PRO" | "ENTERPRISE"
        Defaults to "FREE" if not set
    """
    try:
        user = Users.get_user_by_id(user_id)
        if not user or not user.info:
            return "FREE"
        return user.info.get("subscription_tier", "FREE")
    except Exception:
        return "FREE"


def require_subscription_tier(required_tier: str):
    """
    Dependency to enforce minimum subscription tier.

    Usage:
        @router.get("/premium-feature", dependencies=[Depends(require_subscription_tier("PRO"))])
        async def premium_feature(user=Depends(get_current_user)):
            ...

    Tier hierarchy: FREE < PRO < ENTERPRISE
    """
    tier_hierarchy = {"FREE": 0, "PRO": 1, "ENTERPRISE": 2}

    async def tier_validator(user=Depends(get_current_user)):
        user_tier = get_user_subscription_tier(user.id)
        required_level = tier_hierarchy.get(required_tier, 999)
        user_level = tier_hierarchy.get(user_tier, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires {required_tier} subscription. Your tier: {user_tier}"
            )

        return user

    return tier_validator
```

#### 5.1: Apply Tier Restrictions to Endpoints

Example usage in routers:

```python
# Example: Restrict advanced model access to PRO+ users
@router.post("/chat/completions", dependencies=[Depends(require_subscription_tier("PRO"))])
async def chat_completion(request: Request, user=Depends(get_current_user)):
    # Only PRO and ENTERPRISE users can access
    pass

# Example: Restrict admin panel to ENTERPRISE
@router.get("/admin/analytics", dependencies=[Depends(require_subscription_tier("ENTERPRISE"))])
async def admin_analytics(user=Depends(get_current_user)):
    # Only ENTERPRISE users can access
    pass
```

### Step 6: Configuration

#### 6.1: Environment Variables

Add to `.env` file in `Product/owui/`:

```bash
# Billing Integration
BILLING_JWT_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----
<paste public key from billing stack>
-----END PUBLIC KEY-----"

BILLING_API_URL="https://qdz1hzomxg.execute-api.us-east-1.amazonaws.com/"

# Disable standard signup (use billing-only signup)
ENABLE_SIGNUP=false
ENABLE_LOGIN_FORM=false  # Billing token is the only auth method
```

#### 6.2: Get Public Key from Billing Stack

```bash
# Export public key from billing stack
aws secretsmanager get-secret-value \
  --profile TPAI-Services-admin \
  --secret-id tpai/billing/jwt/public-key \
  --query 'SecretString' \
  --output text > billing-public-key.pem

# Format for .env file (single line with \n)
awk 'NF {sub(/\r/, ""); printf "%s\\n",$0;}' billing-public-key.pem
```

### Step 7: Frontend Integration

#### 7.1: Create Billing Signup Page

**File**: `Product/owui/src/routes/billing/+page.svelte` (new file)

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	let billingToken = '';
	let name = '';
	let error = '';
	let loading = false;

	onMount(() => {
		// Get billing token from URL query parameter
		const params = new URLSearchParams(window.location.search);
		billingToken = params.get('token') || '';

		if (!billingToken) {
			error = 'No billing token provided. Please complete payment first.';
		}
	});

	async function handleSignup() {
		if (!name.trim()) {
			error = 'Please enter your name';
			return;
		}

		loading = true;
		error = '';

		try {
			const response = await fetch('/api/v1/auths/signup/billing', {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					billing_token: billingToken,
					name: name.trim(),
					profile_image_url: '/user.png'
				})
			});

			if (response.ok) {
				const data = await response.json();
				// Token is set as httponly cookie, redirect to app
				goto('/');
			} else {
				const data = await response.json();
				error = data.detail || 'Signup failed. Please try again.';
			}
		} catch (err) {
			error = 'Network error. Please check your connection.';
		} finally {
			loading = false;
		}
	}
</script>

<div class="container">
	<h1>Complete Your Account Setup</h1>

	{#if error}
		<div class="error">{error}</div>
	{/if}

	<form on:submit|preventDefault={handleSignup}>
		<label>
			Your Name:
			<input
				type="text"
				bind:value={name}
				placeholder="Enter your name"
				required
				disabled={loading || !billingToken}
			/>
		</label>

		<button type="submit" disabled={loading || !billingToken}>
			{loading ? 'Creating Account...' : 'Complete Setup'}
		</button>
	</form>

	<p class="info">Your subscription is active. Complete this one-time setup to access TPAI.</p>
</div>

<style>
	.container {
		max-width: 500px;
		margin: 100px auto;
		padding: 2rem;
		border-radius: 8px;
		box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
	}

	.error {
		background: #fee;
		color: #c00;
		padding: 1rem;
		border-radius: 4px;
		margin-bottom: 1rem;
	}

	label {
		display: block;
		margin-bottom: 1rem;
	}

	input {
		width: 100%;
		padding: 0.75rem;
		margin-top: 0.5rem;
		border: 1px solid #ddd;
		border-radius: 4px;
		font-size: 1rem;
	}

	button {
		width: 100%;
		padding: 0.75rem;
		background: #007bff;
		color: white;
		border: none;
		border-radius: 4px;
		font-size: 1rem;
		cursor: pointer;
	}

	button:disabled {
		background: #ccc;
		cursor: not-allowed;
	}

	.info {
		margin-top: 1rem;
		text-align: center;
		color: #666;
		font-size: 0.9rem;
	}
</style>
```

#### 7.2: Update Stripe Checkout Success URL

In billing stack, update Stripe checkout session to redirect to:

```
https://owui.tpai.local/billing?token={ACCESS_TOKEN}
```

Where `{ACCESS_TOKEN}` is the JWT generated by the billing access-token Lambda.

### Step 8: Testing

#### 8.1: Unit Tests

Create `Product/owui/backend/open_webui/test/routers/test_billing_auth.py`:

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

def test_billing_signup_success(client: TestClient):
    """Test successful signup with valid billing token"""

    # Mock billing token validation
    with patch('open_webui.utils.auth.decode_billing_token') as mock_decode:
        mock_decode.return_value = {
            "customer_id": "test-uuid-1234",
            "subscription_tier": "PRO",
            "subscription_status": "active",
            "exp": 1234567890
        }

        response = client.post("/api/v1/auths/signup/billing", json={
            "billing_token": "fake-jwt-token",
            "name": "Test User"
        })

        assert response.status_code == 200
        data = response.json()
        assert "token" in data
        assert data["name"] == "Test User"

def test_billing_signup_invalid_token(client: TestClient):
    """Test signup with invalid billing token"""

    with patch('open_webui.utils.auth.decode_billing_token') as mock_decode:
        mock_decode.return_value = None  # Invalid token

        response = client.post("/api/v1/auths/signup/billing", json={
            "billing_token": "invalid-token",
            "name": "Test User"
        })

        assert response.status_code == 401

def test_tier_restriction(client: TestClient, authenticated_user):
    """Test tier-based access restriction"""

    # Mock user with FREE tier
    with patch('open_webui.utils.middleware.get_user_subscription_tier') as mock_tier:
        mock_tier.return_value = "FREE"

        response = client.get("/api/v1/premium-endpoint")
        assert response.status_code == 403
```

#### 8.2: Integration Test

```bash
# 1. Start OWUI locally
cd Product/owui
docker-compose up

# 2. Generate test billing token
curl -X POST https://qdz1hzomxg.execute-api.us-east-1.amazonaws.com/billing/v1/access-token \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com"}' \
  | jq -r '.access_token'

# 3. Test billing signup
curl -X POST http://localhost:8080/api/v1/auths/signup/billing \
  -H "Content-Type: application/json" \
  -d '{
    "billing_token": "<token-from-step-2>",
    "name": "Test User"
  }'

# 4. Verify user created
# Check database for user with billing_customer_id
```

## Subscription Tier Definitions

### FREE Tier

- **Features**:
  - Basic chat interface
  - Limited model access (open-source models only)
  - 50 messages per day
  - No document uploads
  - No API access

### PRO Tier

- **Features**:
  - All FREE features
  - Advanced model access (Claude, GPT-4, etc.)
  - Unlimited messages
  - Document uploads (PDF, DOCX)
  - Basic API access
  - Priority support

### ENTERPRISE Tier

- **Features**:
  - All PRO features
  - Custom model fine-tuning
  - Advanced analytics dashboard
  - Unlimited API access
  - Dedicated support
  - SSO integration
  - SLA guarantees

## Security Considerations

### Token Validation

- ✅ JWT signature verification (RS256 with public key)
- ✅ Expiration validation (24-hour tokens)
- ✅ No plaintext secrets in code (environment variables)
- ✅ No direct database access to billing system (cross-account isolation)

### PII Protection

- ✅ User emails NOT stored in OWUI (generated emails like `uuid@billing.tpai.local`)
- ✅ Billing customer_id is a UUID (not personally identifiable)
- ✅ HMAC-SHA256 hashing in billing system (original email never leaves billing account)

### Access Control

- ✅ Tier-based permissions enforced at route level
- ✅ Grace period allows 7-day access after cancellation
- ✅ Expired subscriptions denied access (except grace period)

## Troubleshooting

### Issue: "Invalid or expired billing token"

**Causes**:

1. Token expired (>24 hours old)
2. Public key mismatch
3. Token not signed by billing system

**Solutions**:

```bash
# 1. Verify public key in OWUI matches billing system
aws secretsmanager get-secret-value \
  --profile TPAI-Services-admin \
  --secret-id tpai/billing/jwt/public-key \
  --query 'SecretString' \
  --output text

# 2. Check OWUI environment variable
echo $BILLING_JWT_PUBLIC_KEY

# 3. Request new token (tokens expire after 24 hours)
```

### Issue: User created but can't access PRO features

**Cause**: Subscription tier not properly set in user.info

**Solution**:

```python
# Check user's subscription tier
user = Users.get_user_by_id(user_id)
print(user.info)  # Should show {"subscription_tier": "PRO"}

# Manually update if needed
Users.update_user_by_id(user_id, {
    "info": {"subscription_tier": "PRO"}
})
```

### Issue: Database migration fails

**Cause**: Existing user table conflicts

**Solution**:

```bash
# Check current migration version
cd Product/owui/backend
alembic current

# Run migration
alembic upgrade head

# If fails, check migration logs
alembic history
```

## Deployment Checklist

- [ ] Database migration applied (`alembic upgrade head`)
- [ ] `BILLING_JWT_PUBLIC_KEY` environment variable set
- [ ] `BILLING_API_URL` environment variable set
- [ ] `ENABLE_SIGNUP=false` (billing-only auth)
- [ ] Frontend billing signup page deployed
- [ ] Stripe checkout success URL updated
- [ ] Integration tests passing
- [ ] Monitoring alerts configured
- [ ] Documentation updated

## Monitoring

### Key Metrics to Monitor

1. **Billing Signup Success Rate**:

   ```python
   # Track successful /signup/billing calls
   billing_signups_total = Counter("billing_signups_total", "Total billing signups")
   billing_signup_failures = Counter("billing_signup_failures", "Failed billing signups")
   ```

2. **Tier Distribution**:

   ```sql
   SELECT
     info->>'subscription_tier' as tier,
     COUNT(*) as user_count
   FROM user
   WHERE billing_customer_id IS NOT NULL
   GROUP BY tier;
   ```

3. **Token Validation Failures**:
   ```python
   # Monitor decode_billing_token failures
   billing_token_validation_failures = Counter(
       "billing_token_validation_failures",
       "Failed billing token validations",
       ["reason"]  # expired, invalid_signature, missing_fields
   )
   ```

## References

- **Billing Stack README**: `Product/cdk/billing-stack/README.md`
- **Stripe Webhook Setup**: `scripts/billing-tests/STRIPE_WEBHOOK_SETUP.md`
- **Test Execution Plan**: `scripts/billing-tests/EXECUTION_PLAN.md`
- **OWUI Documentation**: `Product/owui/README.md`
