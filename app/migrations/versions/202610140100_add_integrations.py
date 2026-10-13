"""add integrations

Slack, Microsoft Teams and Microsoft 365 connections, channel destinations, linked users, the outbox the
integrations worker sends from, and calendar feeds and Outlook calendar sync.

Revision ID: 202610140100
Revises: 202610130600
Create Date: 2026-10-14 01:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610140100"
down_revision: Union[str, Sequence[str], None] = "202610130600"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('integration_conversations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('tenant_id', sa.String(length=100), nullable=False),
        sa.Column('external_id', sa.String(length=300), nullable=False),
        sa.Column('service_url', sa.String(length=500), nullable=False),
        sa.Column('team_id', sa.String(length=300), nullable=True),
        sa.Column('team_name', sa.String(length=255), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('user_external_id', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind in ('CHANNEL', 'PERSONAL')", name='ck_integration_conversations_kind'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'external_id', name='uq_integration_conversations_provider_external')
        )

    op.create_index('ix_integration_conversations_tenant', 'integration_conversations', ['provider', 'tenant_id'], unique=False)

    op.create_table('integration_state',
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('key')
        )

    op.create_table('calendar_feeds',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
        sa.UniqueConstraint('token_hash')
        )

    op.create_table('calendar_sync_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('source_key', sa.String(length=100), nullable=False),
        sa.Column('event_id', sa.String(length=500), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'source_key', name='uq_calendar_sync_events_user_source')
        )

    op.create_table('microsoft_calendar_connections',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('microsoft_user_id', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('encrypted_token', sa.LargeBinary(), nullable=False),
        sa.Column('calendar_id', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status in ('ACTIVE', 'ERROR')", name='ck_microsoft_calendar_connections_status'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id')
        )

    op.create_table('integration_connections',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('external_id', sa.String(length=100), nullable=False),
        sa.Column('external_name', sa.String(length=255), nullable=True),
        sa.Column('encrypted_token', sa.LargeBinary(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('connected_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("provider in ('SLACK', 'TEAMS', 'MICROSOFT_MAIL')", name='ck_integration_connections_provider'),
        sa.CheckConstraint("status in ('ACTIVE', 'ERROR')", name='ck_integration_connections_status'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['connected_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id', 'provider', name='uq_integration_connections_account_provider')
        )

    op.create_index('ix_integration_connections_provider_external', 'integration_connections', ['provider', 'external_id'], unique=False)

    op.create_table('integration_user_links',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.Column('connection_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('external_user_id', sa.String(length=100), nullable=False),
        sa.Column('external_name', sa.String(length=255), nullable=True),
        sa.Column('conversation_id', sa.Uuid(), nullable=True),
        sa.Column('dm_categories', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['connection_id'], ['integration_connections.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_id'], ['integration_conversations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('connection_id', 'external_user_id', name='uq_integration_user_links_connection_external'),
        sa.UniqueConstraint('connection_id', 'user_id', name='uq_integration_user_links_connection_user')
        )

    op.create_table('integration_destinations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.Column('connection_id', sa.Uuid(), nullable=False),
        sa.Column('channel_id', sa.String(length=300), nullable=False),
        sa.Column('channel_name', sa.String(length=255), nullable=False),
        sa.Column('portfolio_id', sa.Uuid(), nullable=True),
        sa.Column('program_id', sa.Uuid(), nullable=True),
        sa.Column('project_id', sa.Uuid(), nullable=True),
        sa.Column('events', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('last_overdue_on', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('(CASE WHEN portfolio_id IS NULL THEN 0 ELSE 1 END + CASE WHEN program_id IS NULL THEN 0 ELSE 1 END + CASE WHEN project_id IS NULL THEN 0 ELSE 1 END) = 1', name='ck_integration_destinations_one_scope'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['connection_id'], ['integration_connections.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['program_id'], ['programs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )

    op.create_index('ix_integration_destinations_account', 'integration_destinations', ['account_id'], unique=False)

    op.create_table('integration_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('destination_id', sa.Uuid(), nullable=True),
        sa.Column('user_link_id', sa.Uuid(), nullable=True),
        sa.Column('activity_id', sa.Uuid(), nullable=True),
        sa.Column('notification_id', sa.Uuid(), nullable=True),
        sa.Column('event', sa.String(length=40), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind in ('ACTIVITY', 'NOTIFICATION', 'OVERDUE')", name='ck_integration_messages_kind'),
        sa.CheckConstraint("status in ('PENDING', 'SENDING', 'SENT', 'SKIPPED', 'FAILED')", name='ck_integration_messages_status'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['activity_id'], ['activity_log.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['destination_id'], ['integration_destinations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_link_id'], ['integration_user_links.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('destination_id', 'activity_id', name='uq_integration_messages_destination_activity'),
        sa.UniqueConstraint('user_link_id', 'notification_id', name='uq_integration_messages_link_notification')
        )

    op.create_index('ix_integration_messages_status_created', 'integration_messages', ['status', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_integration_conversations_tenant', table_name='integration_conversations')
    op.drop_index('ix_integration_connections_provider_external', table_name='integration_connections')
    op.drop_index('ix_integration_destinations_account', table_name='integration_destinations')
    op.drop_index('ix_integration_messages_status_created', table_name='integration_messages')
    op.drop_table('integration_messages')
    op.drop_table('integration_destinations')
    op.drop_table('integration_user_links')
    op.drop_table('integration_conversations')
    op.drop_table('integration_connections')
    op.drop_table('integration_state')
    op.drop_table('calendar_sync_events')
    op.drop_table('microsoft_calendar_connections')
    op.drop_table('calendar_feeds')
