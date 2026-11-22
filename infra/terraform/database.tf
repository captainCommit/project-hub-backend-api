# Postgres for the app. The Lambdas are not in a VPC (no NAT), so the instance is public and protected by
# SSL (verify-full) plus credentials: an IAM token for the app, a password only for migrations.

data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "db" {
  name        = "project-hub-db-postgres"
  description = "Project Hub Postgres (Lambda outside VPC; SSL + password)"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Postgres"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.db_allowed_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Alphanumeric only, so it can go into a URL without escaping.
resource "random_password" "db_admin" {
  length  = 32
  special = false
}

resource "aws_db_instance" "main" {
  identifier          = var.db_instance_identifier
  engine              = "postgres"
  engine_version      = var.db_engine_version
  instance_class      = var.db_instance_class
  snapshot_identifier = var.db_snapshot_identifier

  # Ignored by AWS when restoring from a snapshot (the snapshot's own values are kept).
  db_name  = var.db_snapshot_identifier == null ? var.db_name : null
  username = var.db_snapshot_identifier == null ? var.db_admin_username : null
  password = random_password.db_admin.result

  allocated_storage = var.db_allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  publicly_accessible                 = true
  iam_database_authentication_enabled = true
  vpc_security_group_ids              = [aws_security_group.db.id]
  parameter_group_name                = var.db_parameter_group_name
  ca_cert_identifier                  = "rds-ca-rsa2048-g1"

  multi_az                   = false
  auto_minor_version_upgrade = true
  backup_retention_period    = var.db_backup_retention_days
  backup_window              = "05:14-05:44"
  maintenance_window         = "tue:03:09-tue:03:39"

  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.db_instance_identifier}-final"
  apply_immediately         = true

  lifecycle {
    # Restoring is a one-off; changing the variable afterwards must not replace the database.
    ignore_changes = [snapshot_identifier]
  }
}

locals {
  db_host = aws_db_instance.main.address

  # Migrations (GitHub secret DATABASE_URL). sslmode=require matches what migrations use today.
  database_admin_url = "postgresql+psycopg://${var.db_admin_username}:${random_password.db_admin.result}@${local.db_host}:5432/${var.db_name}?sslmode=require"

  # Lambdas (GitHub variable APP_DATABASE_URL). No password: the app signs in with an IAM token.
  app_database_url = "postgresql+psycopg://${var.db_app_username}@${local.db_host}:5432/${var.db_name}"
}

# The bootstrap script reads this to set the GitHub secret DATABASE_URL and to create the app login.
resource "aws_ssm_parameter" "database_admin_url" {
  name        = "/project-hub/${var.stage}/database-admin-url"
  description = "Project Hub ${var.stage} admin connection string (migrations only)"
  type        = "SecureString"
  value       = local.database_admin_url
}
