# Private bucket for the git-ignored layer that must NEVER live in the public
# repo: settings_local.py, rules/_private/, data/watchlist.xls. The box pulls
# these on boot (see user_data). Also holds nightly candidate/alert archives.
#
# A public `git clone` is deliberately incomplete; this bucket is how the real
# strategy reaches the box, out-of-band.
resource "aws_s3_bucket" "overlay" {
  bucket_prefix = "${local.name}-overlay-"

  tags = { Name = "${local.name}-overlay" }
}

# Block every form of public access — this holds the strategy IP.
resource "aws_s3_bucket_public_access_block" "overlay" {
  bucket                  = aws_s3_bucket.overlay.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "overlay" {
  bucket = aws_s3_bucket.overlay.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "overlay" {
  bucket = aws_s3_bucket.overlay.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Expire archived candidate/alert files after 90 days; the private overlay lives
# under private/ and is left untouched by this rule.
resource "aws_s3_bucket_lifecycle_configuration" "overlay" {
  bucket = aws_s3_bucket.overlay.id
  rule {
    id     = "expire-archives"
    status = "Enabled"
    filter {
      prefix = "archive/"
    }
    expiration {
      days = 90
    }
  }
}
