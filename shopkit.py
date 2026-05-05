#!/usr/bin/env python3
"""ShopKit - Production-ready PHP/MySQL e-commerce shop deployer with BTCPay Server Bitcoin checkout."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac as _hmac
import json
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

SHOPKIT_VERSION = "1.0.0"
BASE_DIR = Path("/var/www/shopkit")
PUBLIC_DIR = BASE_DIR / "public"
SRC_DIR = BASE_DIR / "src"
TEMPLATES_DIR = BASE_DIR / "templates"
SQL_DIR = BASE_DIR / "sql"
ENV_DIR = Path("/etc/shopkit")
ENV_FILE = ENV_DIR / "shopkit.env"
APP_ENV_FILE = BASE_DIR / ".env"
LOG_DIR = Path("/var/log/shopkit")
BACKUP_DIR = Path("/var/backups/shopkit")
NGINX_CONF = Path("/etc/nginx/sites-available/shopkit")
NGINX_ENABLED = Path("/etc/nginx/sites-enabled/shopkit")
SYSTEMD_SERVICE = Path("/etc/systemd/system/shopkit-watchdog.service")
SYSTEMD_TIMER = Path("/etc/systemd/system/shopkit-watchdog.timer")
LOGROTATE_CONF = Path("/etc/logrotate.d/shopkit")
BOOTSTRAP_CSS_URL = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
BOOTSTRAP_JS_URL = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"
BOOTSTRAP_CSS_SHA = "sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH"
BOOTSTRAP_JS_SHA = "sha384-YvpcrYf0tY3lHB60NNkmXc4s9bIOgUxi8T/jzmA2bI4wjCUIDH5x3+a9vJ7dMpob"

PINNED_DEPS = [
    "requests==2.32.3",
    "mysql-connector-python==9.0.0",
    "python-dotenv==1.0.1",
    "cryptography==43.0.1",
]

logger = logging.getLogger("shopkit")

# ---------------------------------------------------------------------------
# SQL SCHEMA
# ---------------------------------------------------------------------------
SQL_SCHEMA = """
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE IF NOT EXISTS `users` (
  `id`            INT UNSIGNED    NOT NULL AUTO_INCREMENT,
  `name`          VARCHAR(255)    NOT NULL,
  `email`         VARCHAR(255)    NOT NULL,
  `password_hash` VARCHAR(255)    NOT NULL,
  `is_admin`      TINYINT(1)      NOT NULL DEFAULT 0,
  `is_active`     TINYINT(1)      NOT NULL DEFAULT 1,
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_email` (`email`),
  KEY `idx_users_is_admin` (`is_admin`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `categories` (
  `id`          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `name`        VARCHAR(255)  NOT NULL,
  `slug`        VARCHAR(255)  NOT NULL,
  `description` TEXT,
  `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_categories_slug` (`slug`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `products` (
  `id`          INT UNSIGNED    NOT NULL AUTO_INCREMENT,
  `category_id` INT UNSIGNED    DEFAULT NULL,
  `name`        VARCHAR(255)    NOT NULL,
  `slug`        VARCHAR(255)    NOT NULL,
  `description` TEXT,
  `price`       DECIMAL(10,2)   NOT NULL DEFAULT 0.00,
  `stock`       INT             NOT NULL DEFAULT 0,
  `image`       VARCHAR(500)    DEFAULT NULL,
  `is_active`   TINYINT(1)      NOT NULL DEFAULT 1,
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_products_slug` (`slug`),
  KEY `idx_products_category_id` (`category_id`),
  KEY `idx_products_is_active` (`is_active`),
  CONSTRAINT `fk_product_category`
    FOREIGN KEY (`category_id`) REFERENCES `categories` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `orders` (
  `id`                 INT UNSIGNED   NOT NULL AUTO_INCREMENT,
  `user_id`            INT UNSIGNED   DEFAULT NULL,
  `order_number`       VARCHAR(50)    NOT NULL,
  `status`             ENUM('pending','paid','processing','shipped','completed','cancelled','refunded')
                         NOT NULL DEFAULT 'pending',
  `total`              DECIMAL(10,2)  NOT NULL DEFAULT 0.00,
  `currency`           VARCHAR(10)    NOT NULL DEFAULT 'USD',
  `btcpay_invoice_id`  VARCHAR(255)   DEFAULT NULL,
  `btcpay_invoice_url` VARCHAR(500)   DEFAULT NULL,
  `customer_email`     VARCHAR(255)   NOT NULL,
  `customer_name`      VARCHAR(255)   NOT NULL,
  `shipping_address`   TEXT,
  `notes`              TEXT,
  `created_at`         DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`         DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_orders_order_number` (`order_number`),
  KEY `idx_orders_user_id` (`user_id`),
  KEY `idx_orders_status` (`status`),
  KEY `idx_orders_btcpay_invoice_id` (`btcpay_invoice_id`),
  CONSTRAINT `fk_order_user`
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `order_items` (
  `id`           INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `order_id`     INT UNSIGNED  NOT NULL,
  `product_id`   INT UNSIGNED  DEFAULT NULL,
  `product_name` VARCHAR(255)  NOT NULL,
  `price`        DECIMAL(10,2) NOT NULL,
  `quantity`     INT           NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  KEY `idx_order_items_order_id` (`order_id`),
  KEY `idx_order_items_product_id` (`product_id`),
  CONSTRAINT `fk_item_order`   FOREIGN KEY (`order_id`)   REFERENCES `orders`   (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_item_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sessions` (
  `id`         VARCHAR(128) NOT NULL,
  `user_id`    INT UNSIGNED DEFAULT NULL,
  `data`       MEDIUMTEXT,
  `expires_at` DATETIME     NOT NULL,
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_sessions_user_id`   (`user_id`),
  KEY `idx_sessions_expires_at` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `settings` (
  `key`        VARCHAR(100) NOT NULL,
  `value`      TEXT,
  `updated_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `webhook_log` (
  `id`         INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `event_type` VARCHAR(100)  DEFAULT NULL,
  `invoice_id` VARCHAR(255)  DEFAULT NULL,
  `payload`    MEDIUMTEXT,
  `status`     VARCHAR(50)   DEFAULT NULL,
  `created_at` DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_webhook_log_invoice_id`  (`invoice_id`),
  KEY `idx_webhook_log_event_type`  (`event_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `login_attempts` (
  `id`           INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `email`        VARCHAR(255)  NOT NULL,
  `ip_address`   VARCHAR(45)   NOT NULL,
  `attempted_at` DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_login_attempts_email` (`email`),
  KEY `idx_login_attempts_ip`    (`ip_address`),
  KEY `idx_login_attempts_at`    (`attempted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

INSERT IGNORE INTO `categories` (`name`, `slug`, `description`) VALUES
  ('Electronics', 'electronics', 'Electronic devices and accessories'),
  ('Clothing',    'clothing',    'Apparel and fashion items');

INSERT IGNORE INTO `products` (`category_id`, `name`, `slug`, `description`, `price`, `stock`, `is_active`) VALUES
  (1, 'Bitcoin Hardware Wallet', 'bitcoin-hardware-wallet',
   'Secure hardware wallet for storing Bitcoin and other cryptocurrencies safely offline.', 79.99,  50, 1),
  (1, 'Lightning Node Kit',      'lightning-node-kit',
   'Complete kit to run your own Lightning Network node at home.',                          149.99, 25, 1),
  (2, 'Bitcoin T-Shirt',         'bitcoin-tshirt',
   'Premium cotton t-shirt with Bitcoin logo. Available in multiple sizes.',                24.99, 100, 1);

INSERT IGNORE INTO `settings` (`key`, `value`) VALUES
  ('shop_name',                 'My Shop'),
  ('shop_currency',             'USD'),
  ('items_per_page',            '12'),
  ('maintenance_mode',          '0'),
  ('allow_registration',        '1'),
  ('order_email_notifications', '1');
"""


# ---------------------------------------------------------------------------
# PHP FILES (raw strings to preserve backslashes and PHP syntax)
# ---------------------------------------------------------------------------
PHP_FILES: dict[str, str] = {}

# ------ src/Config.php ------
PHP_FILES["src/Config.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Config
{
    private static array $data = [];
    private static bool  $loaded = false;

    public static function load(): void
    {
        if (self::$loaded) {
            return;
        }
        $envFile = '/etc/shopkit/shopkit.env';
        if (is_readable($envFile)) {
            foreach (file($envFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
                if (str_starts_with(trim($line), '#') || !str_contains($line, '=')) {
                    continue;
                }
                [$k, $v] = array_map('trim', explode('=', $line, 2));
                if ($k !== '' && !isset($_ENV[$k])) {
                    self::$data[$k] = $v;
                    $_ENV[$k]       = $v;
                    putenv("$k=$v");
                }
            }
        }
        self::$loaded = true;
    }

    public static function get(string $key, mixed $default = null): mixed
    {
        self::load();
        return self::$data[$key] ?? $_ENV[$key] ?? getenv($key) ?: $default;
    }

    public static function required(string $key): string
    {
        $val = self::get($key);
        if ($val === null || $val === '') {
            throw new \RuntimeException("Required config key '$key' is missing.");
        }
        return (string)$val;
    }
}
"""

# ------ src/Database.php ------
PHP_FILES["src/Database.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Database
{
    private static ?\PDO $instance = null;

    public static function getInstance(): \PDO
    {
        if (self::$instance === null) {
            Config::load();
            $dsn = sprintf(
                'mysql:host=%s;port=%s;dbname=%s;charset=utf8mb4',
                Config::required('DB_HOST'),
                Config::get('DB_PORT', '3306'),
                Config::required('DB_NAME')
            );
            $options = [
                \PDO::ATTR_ERRMODE            => \PDO::ERRMODE_EXCEPTION,
                \PDO::ATTR_DEFAULT_FETCH_MODE => \PDO::FETCH_ASSOC,
                \PDO::ATTR_EMULATE_PREPARES   => false,
                \PDO::MYSQL_ATTR_INIT_COMMAND => "SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
            ];
            self::$instance = new \PDO(
                $dsn,
                Config::required('DB_USER'),
                Config::required('DB_PASS'),
                $options
            );
        }
        return self::$instance;
    }

    public static function query(string $sql, array $params = []): \PDOStatement
    {
        $stmt = self::getInstance()->prepare($sql);
        $stmt->execute($params);
        return $stmt;
    }

    public static function fetchOne(string $sql, array $params = []): ?array
    {
        $row = self::query($sql, $params)->fetch();
        return $row === false ? null : $row;
    }

    public static function fetchAll(string $sql, array $params = []): array
    {
        return self::query($sql, $params)->fetchAll();
    }

    public static function lastInsertId(): int
    {
        return (int)self::getInstance()->lastInsertId();
    }

    public static function beginTransaction(): void
    {
        self::getInstance()->beginTransaction();
    }

    public static function commit(): void
    {
        self::getInstance()->commit();
    }

    public static function rollBack(): void
    {
        self::getInstance()->rollBack();
    }
}
"""

# ------ src/Auth.php ------
PHP_FILES["src/Auth.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Auth
{
    private const SESSION_USER_KEY    = 'auth_user_id';
    private const SESSION_REGEN_KEY   = 'auth_last_regen';
    private const REGEN_INTERVAL      = 1800; // 30 minutes

    public static function startSession(): void
    {
        if (session_status() !== PHP_SESSION_NONE) {
            return;
        }
        session_set_cookie_params([
            'lifetime' => 0,
            'path'     => '/',
            'secure'   => true,
            'httponly' => true,
            'samesite' => 'Lax',
        ]);
        ini_set('session.use_strict_mode',  '1');
        ini_set('session.use_only_cookies', '1');
        session_start();

        // Regenerate session ID periodically
        $now = time();
        if (!isset($_SESSION[self::SESSION_REGEN_KEY])) {
            $_SESSION[self::SESSION_REGEN_KEY] = $now;
        } elseif ($now - $_SESSION[self::SESSION_REGEN_KEY] > self::REGEN_INTERVAL) {
            session_regenerate_id(true);
            $_SESSION[self::SESSION_REGEN_KEY] = $now;
        }
    }

    public static function login(string $email, string $password): bool
    {
        RateLimit::checkLogin($email, $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0');

        $user = Database::fetchOne(
            'SELECT * FROM users WHERE email = ? AND is_active = 1',
            [$email]
        );
        if ($user === null) {
            RateLimit::recordAttempt($email, $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0');
            return false;
        }
        if (!password_verify($password, $user['password_hash'])) {
            RateLimit::recordAttempt($email, $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0');
            return false;
        }
        session_regenerate_id(true);
        $_SESSION[self::SESSION_USER_KEY]  = $user['id'];
        $_SESSION[self::SESSION_REGEN_KEY] = time();
        return true;
    }

    public static function logout(): void
    {
        session_unset();
        session_destroy();
    }

    public static function check(): bool
    {
        return isset($_SESSION[self::SESSION_USER_KEY]);
    }

    public static function user(): ?array
    {
        if (!self::check()) {
            return null;
        }
        return Database::fetchOne(
            'SELECT id, name, email, is_admin FROM users WHERE id = ? AND is_active = 1',
            [$_SESSION[self::SESSION_USER_KEY]]
        );
    }

    public static function requireLogin(): array
    {
        $user = self::user();
        if ($user === null) {
            header('Location: /login.php?redirect=' . urlencode($_SERVER['REQUEST_URI'] ?? '/'));
            exit;
        }
        return $user;
    }

    public static function requireAdmin(): array
    {
        $user = self::requireLogin();
        if (!$user['is_admin']) {
            http_response_code(403);
            die('Access denied.');
        }
        return $user;
    }

    public static function register(string $name, string $email, string $password): int
    {
        $existing = Database::fetchOne('SELECT id FROM users WHERE email = ?', [$email]);
        if ($existing !== null) {
            throw new \RuntimeException('Email already registered.');
        }
        $hash = password_hash($password, PASSWORD_ARGON2ID);
        Database::query(
            'INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)',
            [$name, $email, $hash]
        );
        return Database::lastInsertId();
    }

    public static function hashPassword(string $password): string
    {
        return password_hash($password, PASSWORD_ARGON2ID);
    }
}
"""

# ------ src/Csrf.php ------
PHP_FILES["src/Csrf.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Csrf
{
    private const TOKEN_KEY    = '_csrf_token';
    private const FIELD_NAME   = 'csrf_token';
    private const TOKEN_BYTES  = 32;

    public static function token(): string
    {
        if (empty($_SESSION[self::TOKEN_KEY])) {
            $_SESSION[self::TOKEN_KEY] = bin2hex(random_bytes(self::TOKEN_BYTES));
        }
        return $_SESSION[self::TOKEN_KEY];
    }

    public static function field(): string
    {
        return '<input type="hidden" name="' . self::FIELD_NAME
            . '" value="' . htmlspecialchars(self::token(), ENT_QUOTES) . '">';
    }

    public static function verify(): void
    {
        $submitted = $_POST[self::FIELD_NAME] ?? '';
        if (!hash_equals(self::token(), $submitted)) {
            http_response_code(403);
            die('Invalid CSRF token.');
        }
    }
}
"""

# ------ src/Cart.php ------
PHP_FILES["src/Cart.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Cart
{
    private const SESSION_KEY = '_cart';

    private static function &items(): array
    {
        if (!isset($_SESSION[self::SESSION_KEY])) {
            $_SESSION[self::SESSION_KEY] = [];
        }
        return $_SESSION[self::SESSION_KEY];
    }

    public static function add(int $productId, int $qty = 1): void
    {
        $product = Product::findById($productId);
        if ($product === null || !$product['is_active'] || $product['stock'] < 1) {
            return;
        }
        $items = &self::items();
        if (isset($items[$productId])) {
            $items[$productId]['qty'] = min(
                $items[$productId]['qty'] + $qty,
                $product['stock']
            );
        } else {
            $items[$productId] = [
                'product_id' => $productId,
                'name'       => $product['name'],
                'price'      => (float)$product['price'],
                'qty'        => min($qty, $product['stock']),
                'image'      => $product['image'] ?? '',
            ];
        }
    }

    public static function update(int $productId, int $qty): void
    {
        $items = &self::items();
        if ($qty <= 0) {
            unset($items[$productId]);
            return;
        }
        $product = Product::findById($productId);
        if ($product) {
            $items[$productId]['qty'] = min($qty, $product['stock']);
        }
    }

    public static function remove(int $productId): void
    {
        $items = &self::items();
        unset($items[$productId]);
    }

    public static function clear(): void
    {
        $_SESSION[self::SESSION_KEY] = [];
    }

    public static function getItems(): array
    {
        return self::items();
    }

    public static function total(): float
    {
        $total = 0.0;
        foreach (self::items() as $item) {
            $total += $item['price'] * $item['qty'];
        }
        return round($total, 2);
    }

    public static function count(): int
    {
        $count = 0;
        foreach (self::items() as $item) {
            $count += $item['qty'];
        }
        return $count;
    }

    public static function isEmpty(): bool
    {
        return empty(self::items());
    }
}
"""

# ------ src/Product.php ------
PHP_FILES["src/Product.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Product
{
    public static function findById(int $id): ?array
    {
        return Database::fetchOne(
            'SELECT p.*, c.name AS category_name, c.slug AS category_slug
             FROM products p LEFT JOIN categories c ON c.id = p.category_id
             WHERE p.id = ?',
            [$id]
        );
    }

    public static function findBySlug(string $slug): ?array
    {
        return Database::fetchOne(
            'SELECT p.*, c.name AS category_name, c.slug AS category_slug
             FROM products p LEFT JOIN categories c ON c.id = p.category_id
             WHERE p.slug = ? AND p.is_active = 1',
            [$slug]
        );
    }

    public static function listActive(int $page = 1, int $perPage = 12, ?int $categoryId = null): array
    {
        $offset = ($page - 1) * $perPage;
        $params = [];
        $where  = 'p.is_active = 1';
        if ($categoryId !== null) {
            $where   .= ' AND p.category_id = ?';
            $params[] = $categoryId;
        }
        $params[] = $perPage;
        $params[] = $offset;
        return Database::fetchAll(
            "SELECT p.*, c.name AS category_name FROM products p
             LEFT JOIN categories c ON c.id = p.category_id
             WHERE $where ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
            $params
        );
    }

    public static function countActive(?int $categoryId = null): int
    {
        $params = [];
        $where  = 'is_active = 1';
        if ($categoryId !== null) {
            $where   .= ' AND category_id = ?';
            $params[] = $categoryId;
        }
        $row = Database::fetchOne("SELECT COUNT(*) AS n FROM products WHERE $where", $params);
        return (int)($row['n'] ?? 0);
    }

    public static function search(string $q, int $page = 1, int $perPage = 12): array
    {
        $offset = ($page - 1) * $perPage;
        $like   = '%' . $q . '%';
        return Database::fetchAll(
            'SELECT p.*, c.name AS category_name FROM products p
             LEFT JOIN categories c ON c.id = p.category_id
             WHERE p.is_active = 1 AND (p.name LIKE ? OR p.description LIKE ?)
             ORDER BY p.name ASC LIMIT ? OFFSET ?',
            [$like, $like, $perPage, $offset]
        );
    }

    public static function allCategories(): array
    {
        return Database::fetchAll('SELECT * FROM categories ORDER BY name ASC');
    }

    public static function decrementStock(int $productId, int $qty): void
    {
        Database::query(
            'UPDATE products SET stock = GREATEST(0, stock - ?) WHERE id = ?',
            [$qty, $productId]
        );
    }
}
"""

# ------ src/Order.php ------
PHP_FILES["src/Order.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Order
{
    public static function create(array $customer, ?int $userId): int
    {
        $orderNumber = 'ORD-' . strtoupper(bin2hex(random_bytes(6)));
        $items       = Cart::getItems();
        if (empty($items)) {
            throw new \RuntimeException('Cart is empty.');
        }
        $total = Cart::total();

        Database::beginTransaction();
        try {
            Database::query(
                'INSERT INTO orders (user_id, order_number, status, total, currency, customer_email, customer_name, shipping_address)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    $userId,
                    $orderNumber,
                    'pending',
                    $total,
                    Config::get('SHOP_CURRENCY', 'USD'),
                    $customer['email'],
                    $customer['name'],
                    $customer['address'] ?? '',
                ]
            );
            $orderId = Database::lastInsertId();
            foreach ($items as $item) {
                Database::query(
                    'INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
                     VALUES (?, ?, ?, ?, ?)',
                    [$orderId, $item['product_id'], $item['name'], $item['price'], $item['qty']]
                );
                Product::decrementStock($item['product_id'], $item['qty']);
            }
            Database::commit();
        } catch (\Throwable $e) {
            Database::rollBack();
            throw $e;
        }
        return $orderId;
    }

    public static function findById(int $id): ?array
    {
        return Database::fetchOne('SELECT * FROM orders WHERE id = ?', [$id]);
    }

    public static function findByOrderNumber(string $orderNumber): ?array
    {
        return Database::fetchOne('SELECT * FROM orders WHERE order_number = ?', [$orderNumber]);
    }

    public static function itemsFor(int $orderId): array
    {
        return Database::fetchAll(
            'SELECT * FROM order_items WHERE order_id = ?',
            [$orderId]
        );
    }

    public static function updateStatus(int $id, string $status): void
    {
        Database::query('UPDATE orders SET status = ? WHERE id = ?', [$status, $id]);
    }

    public static function attachInvoice(int $id, string $invoiceId, string $invoiceUrl): void
    {
        Database::query(
            'UPDATE orders SET btcpay_invoice_id = ?, btcpay_invoice_url = ? WHERE id = ?',
            [$invoiceId, $invoiceUrl, $id]
        );
    }

    public static function listForUser(int $userId): array
    {
        return Database::fetchAll(
            'SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC',
            [$userId]
        );
    }

    public static function listAll(int $page = 1, int $perPage = 20): array
    {
        $offset = ($page - 1) * $perPage;
        return Database::fetchAll(
            'SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?',
            [$perPage, $offset]
        );
    }

    public static function countAll(): int
    {
        $row = Database::fetchOne('SELECT COUNT(*) AS n FROM orders');
        return (int)($row['n'] ?? 0);
    }
}
"""

# ------ src/BTCPayClient.php ------
PHP_FILES["src/BTCPayClient.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class BTCPayClient
{
    private string $host;
    private string $apiKey;
    private string $storeId;

    public function __construct()
    {
        $this->host    = rtrim(Config::required('BTCPAY_HOST'), '/');
        $this->apiKey  = Config::required('BTCPAY_API_KEY');
        $this->storeId = Config::required('BTCPAY_STORE_ID');
    }

    public function createInvoice(
        float  $amount,
        string $currency,
        int    $orderId,
        string $orderNumber,
        string $customerEmail,
        string $redirectUrl,
        string $webhookUrl
    ): array {
        $payload = [
            'amount'   => (string)$amount,
            'currency' => $currency,
            'metadata' => [
                'orderId'       => $orderId,
                'orderNumber'   => $orderNumber,
                'customerEmail' => $customerEmail,
            ],
            'checkout' => [
                'redirectURL'          => $redirectUrl,
                'redirectAutomatically' => true,
                'defaultPaymentMethod' => 'BTC',
            ],
            'additionalSearchTerms' => [$orderNumber, $customerEmail],
        ];

        $response = $this->request(
            'POST',
            "/api/v1/stores/{$this->storeId}/invoices",
            $payload
        );
        return $response;
    }

    public function getInvoice(string $invoiceId): array
    {
        return $this->request('GET', "/api/v1/stores/{$this->storeId}/invoices/{$invoiceId}");
    }

    public static function verifyWebhook(string $rawBody, string $signatureHeader, string $secret): bool
    {
        $expected = 'sha256=' . hash_hmac('sha256', $rawBody, $secret);
        return hash_equals($expected, $signatureHeader);
    }

    private function request(string $method, string $path, array $body = []): array
    {
        $url = $this->host . $path;
        $ch  = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => 30,
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_HTTPHEADER     => [
                'Authorization: token ' . $this->apiKey,
                'Content-Type: application/json',
                'Accept: application/json',
            ],
        ]);
        if ($method === 'POST') {
            curl_setopt($ch, CURLOPT_POST, true);
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
        } elseif ($method !== 'GET') {
            curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
            if (!empty($body)) {
                curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
            }
        }
        $raw  = curl_exec($ch);
        $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err  = curl_error($ch);
        curl_close($ch);
        if ($err) {
            throw new \RuntimeException("BTCPay cURL error: $err");
        }
        $data = json_decode($raw, true);
        if (!is_array($data)) {
            throw new \RuntimeException("BTCPay returned invalid JSON (HTTP $code).");
        }
        if ($code >= 400) {
            $msg = $data['message'] ?? $raw;
            throw new \RuntimeException("BTCPay API error HTTP $code: $msg");
        }
        return $data;
    }
}
"""

# ------ src/RateLimit.php ------
PHP_FILES["src/RateLimit.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class RateLimit
{
    private const MAX_ATTEMPTS  = 5;
    private const WINDOW_MINUTES = 15;

    public static function checkLogin(string $email, string $ip): void
    {
        $since   = (new \DateTime())->modify('-' . self::WINDOW_MINUTES . ' minutes')
                      ->format('Y-m-d H:i:s');
        $row = Database::fetchOne(
            'SELECT COUNT(*) AS n FROM login_attempts
             WHERE (email = ? OR ip_address = ?) AND attempted_at > ?',
            [$email, $ip, $since]
        );
        if ((int)($row['n'] ?? 0) >= self::MAX_ATTEMPTS) {
            http_response_code(429);
            die('Too many login attempts. Please wait ' . self::WINDOW_MINUTES . ' minutes.');
        }
    }

    public static function recordAttempt(string $email, string $ip): void
    {
        Database::query(
            'INSERT INTO login_attempts (email, ip_address) VALUES (?, ?)',
            [$email, $ip]
        );
    }
}
"""

# ------ src/Mailer.php ------
PHP_FILES["src/Mailer.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Mailer
{
    public static function orderConfirmation(array $order, array $items): void
    {
        $shopName = Config::get('SHOP_NAME', 'My Shop');
        $to       = $order['customer_email'];
        $subject  = "[{$shopName}] Order #{$order['order_number']} Confirmed";

        $body  = "Hello {$order['customer_name']},\r\n\r\n";
        $body .= "Thank you for your order!\r\n\r\n";
        $body .= "Order Number: {$order['order_number']}\r\n";
        $body .= "Total: {$order['currency']} " . number_format((float)$order['total'], 2) . "\r\n\r\n";
        $body .= "Items:\r\n";
        foreach ($items as $item) {
            $body .= "  - {$item['product_name']} x{$item['quantity']} @ "
                   . number_format((float)$item['price'], 2) . "\r\n";
        }
        $body .= "\r\nPayment: Bitcoin via BTCPay Server\r\n";
        if (!empty($order['btcpay_invoice_url'])) {
            $body .= "Pay here: {$order['btcpay_invoice_url']}\r\n";
        }
        $body .= "\r\nThank you for shopping with us!\r\n-- {$shopName}\r\n";

        $headers  = "From: noreply@" . (Config::get('SHOP_DOMAIN', 'localhost')) . "\r\n";
        $headers .= "MIME-Version: 1.0\r\n";
        $headers .= "Content-Type: text/plain; charset=UTF-8\r\n";

        @mail($to, $subject, $body, $headers);
    }
}
"""

# ------ src/helpers.php ------
PHP_FILES["src/helpers.php"] = r"""<?php
declare(strict_types=1);

if (!function_exists('e')) {
    function e(mixed $value): string
    {
        return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    }
}

if (!function_exists('redirect')) {
    function redirect(string $url, int $code = 302): never
    {
        header("Location: $url", true, $code);
        exit;
    }
}

if (!function_exists('slugify')) {
    function slugify(string $text): string
    {
        $text = mb_strtolower($text, 'UTF-8');
        $text = preg_replace('/[^\p{L}\p{N}\s-]/u', '', $text);
        $text = preg_replace('/[\s-]+/', '-', trim($text));
        return trim($text, '-');
    }
}

if (!function_exists('paginate')) {
    function paginate(int $total, int $perPage, int $current, string $urlPattern): array
    {
        $pages = (int)ceil($total / max(1, $perPage));
        return [
            'total'      => $total,
            'per_page'   => $perPage,
            'current'    => $current,
            'last_page'  => $pages,
            'has_prev'   => $current > 1,
            'has_next'   => $current < $pages,
            'url_pattern' => $urlPattern,
        ];
    }
}

if (!function_exists('flash_set')) {
    function flash_set(string $type, string $message): void
    {
        $_SESSION['_flash'][$type][] = $message;
    }
}

if (!function_exists('flash_get')) {
    function flash_get(): array
    {
        $flashes = $_SESSION['_flash'] ?? [];
        unset($_SESSION['_flash']);
        return $flashes;
    }
}

if (!function_exists('setting')) {
    function setting(string $key, mixed $default = ''): mixed
    {
        static $cache = [];
        if (!isset($cache[$key])) {
            $row = \ShopKit\Database::fetchOne(
                'SELECT value FROM settings WHERE `key` = ?',
                [$key]
            );
            $cache[$key] = $row !== null ? $row['value'] : $default;
        }
        return $cache[$key];
    }
}
"""

# ------ src/Sitemap.php ------
PHP_FILES["src/Sitemap.php"] = r"""<?php
declare(strict_types=1);

namespace ShopKit;

class Sitemap
{
    public static function generate(string $baseUrl): string
    {
        $baseUrl = rtrim($baseUrl, '/');
        $urls    = [];

        $urls[] = ['loc' => $baseUrl . '/',              'changefreq' => 'daily',   'priority' => '1.0'];
        $urls[] = ['loc' => $baseUrl . '/search.php',    'changefreq' => 'weekly',  'priority' => '0.5'];
        $urls[] = ['loc' => $baseUrl . '/register.php',  'changefreq' => 'monthly', 'priority' => '0.3'];

        foreach (Product::allCategories() as $cat) {
            $urls[] = [
                'loc'        => $baseUrl . '/category.php?slug=' . urlencode($cat['slug']),
                'changefreq' => 'weekly',
                'priority'   => '0.7',
            ];
        }

        $products = Database::fetchAll(
            'SELECT slug, updated_at FROM products WHERE is_active = 1'
        );
        foreach ($products as $p) {
            $urls[] = [
                'loc'      => $baseUrl . '/product.php?slug=' . urlencode($p['slug']),
                'lastmod'  => substr($p['updated_at'], 0, 10),
                'changefreq' => 'weekly',
                'priority'   => '0.8',
            ];
        }

        $xml  = '<?xml version="1.0" encoding="UTF-8"?>' . PHP_EOL;
        $xml .= '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' . PHP_EOL;
        foreach ($urls as $u) {
            $xml .= '  <url>' . PHP_EOL;
            $xml .= '    <loc>' . htmlspecialchars($u['loc'], ENT_XML1) . '</loc>' . PHP_EOL;
            if (isset($u['lastmod'])) {
                $xml .= '    <lastmod>' . $u['lastmod'] . '</lastmod>' . PHP_EOL;
            }
            if (isset($u['changefreq'])) {
                $xml .= '    <changefreq>' . $u['changefreq'] . '</changefreq>' . PHP_EOL;
            }
            if (isset($u['priority'])) {
                $xml .= '    <priority>' . $u['priority'] . '</priority>' . PHP_EOL;
            }
            $xml .= '  </url>' . PHP_EOL;
        }
        $xml .= '</urlset>' . PHP_EOL;
        return $xml;
    }
}
"""

print("PHP src files written to dict")

# ------ templates/layout.php ------
PHP_FILES["templates/layout.php"] = r"""<?php
declare(strict_types=1);
use ShopKit\Auth;
use ShopKit\Cart;
Auth::startSession();
$user      = Auth::user();
$cartCount = Cart::count();
$shopName  = setting('shop_name', 'ShopKit');
$flashes   = flash_get();
?><!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title><?= isset($pageTitle) ? e($pageTitle) . ' &mdash; ' : '' ?><?= e($shopName) ?></title>
  <link rel="stylesheet" href="/assets/css/bootstrap.min.css">
  <link rel="stylesheet" href="/assets/css/app.css">
  <?php if (isset($extraHead)) echo $extraHead; ?>
</head>
<body>
<?php include __DIR__ . '/header.php'; ?>
<main class="container py-4">
<?php foreach ($flashes as $type => $messages): ?>
  <?php foreach ($messages as $msg): ?>
    <div class="alert alert-<?= e($type === 'error' ? 'danger' : $type) ?> alert-dismissible fade show" role="alert">
      <?= e($msg) ?>
      <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    </div>
  <?php endforeach; ?>
<?php endforeach; ?>
"""

# ------ templates/header.php ------
PHP_FILES["templates/header.php"] = r"""<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container">
    <a class="navbar-brand fw-bold" href="/"><?= e($shopName) ?></a>
    <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navMenu">
      <span class="navbar-toggler-icon"></span>
    </button>
    <div class="collapse navbar-collapse" id="navMenu">
      <ul class="navbar-nav me-auto mb-2 mb-lg-0">
        <li class="nav-item"><a class="nav-link" href="/">Home</a></li>
        <li class="nav-item"><a class="nav-link" href="/search.php">Search</a></li>
      </ul>
      <ul class="navbar-nav">
        <li class="nav-item">
          <a class="nav-link" href="/cart.php">
            Cart <span class="badge bg-warning text-dark"><?= (int)$cartCount ?></span>
          </a>
        </li>
        <?php if ($user): ?>
          <?php if ($user['is_admin']): ?>
            <li class="nav-item"><a class="nav-link" href="/admin/">Admin</a></li>
          <?php endif; ?>
          <li class="nav-item"><a class="nav-link" href="/account.php"><?= e($user['name']) ?></a></li>
          <li class="nav-item"><a class="nav-link" href="/logout.php">Logout</a></li>
        <?php else: ?>
          <li class="nav-item"><a class="nav-link" href="/login.php">Login</a></li>
          <li class="nav-item"><a class="nav-link" href="/register.php">Register</a></li>
        <?php endif; ?>
      </ul>
    </div>
  </div>
</nav>
"""

# ------ templates/footer.php ------
PHP_FILES["templates/footer.php"] = r"""</main>
<footer class="bg-dark text-light py-4 mt-5">
  <div class="container text-center">
    <p class="mb-1">&copy; <?= date('Y') ?> <?= e($shopName) ?>. Powered by Bitcoin &amp; BTCPay Server.</p>
    <p class="mb-0 small">
      <a href="/sitemap.php" class="text-light">Sitemap</a> &bull;
      <a href="/robots.txt" class="text-light">Robots</a>
    </p>
  </div>
</footer>
<script src="/assets/js/bootstrap.bundle.min.js"></script>
<script src="/assets/js/app.js"></script>
<?php if (isset($extraScripts)) echo $extraScripts; ?>
</body>
</html>
"""


# ------ public/index.php ------
PHP_FILES["public/index.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Product;
use ShopKit\Database;
$page    = max(1, (int)($_GET['page'] ?? 1));
$perPage = (int)setting('items_per_page', 12);
$catSlug = $_GET['category'] ?? null;
$catId   = null;
if ($catSlug) {
    $cat   = Database::fetchOne('SELECT * FROM categories WHERE slug = ?', [$catSlug]);
    $catId = $cat ? (int)$cat['id'] : null;
}
$products  = Product::listActive($page, $perPage, $catId);
$total     = Product::countActive($catId);
$pagInfo   = paginate($total, $perPage, $page, '/index.php?page={page}');
$pageTitle = 'Home';
include dirname(__DIR__) . '/templates/layout.php';
?>
<div class="row row-cols-1 row-cols-md-3 g-4">
<?php foreach ($products as $p): ?>
  <div class="col">
    <div class="card h-100 shadow-sm">
      <?php if ($p['image']): ?>
        <img src="<?= e($p['image']) ?>" class="card-img-top" alt="<?= e($p['name']) ?>" style="height:200px;object-fit:cover">
      <?php else: ?>
        <div class="card-img-top bg-secondary d-flex align-items-center justify-content-center" style="height:200px">
          <span class="text-white fs-1">&#128722;</span>
        </div>
      <?php endif; ?>
      <div class="card-body">
        <h5 class="card-title"><?= e($p['name']) ?></h5>
        <p class="card-text text-muted small"><?= e(mb_substr($p['description'] ?? '', 0, 100)) ?>...</p>
        <p class="fw-bold text-success"><?= e(setting('shop_currency', 'USD')) ?> <?= number_format((float)$p['price'], 2) ?></p>
      </div>
      <div class="card-footer">
        <a href="/product.php?slug=<?= urlencode($p['slug']) ?>" class="btn btn-primary btn-sm w-100">View Product</a>
      </div>
    </div>
  </div>
<?php endforeach; ?>
</div>
<?php if ($pagInfo['last_page'] > 1): ?>
<nav class="mt-4">
  <ul class="pagination justify-content-center">
    <?php if ($pagInfo['has_prev']): ?>
      <li class="page-item"><a class="page-link" href="/?page=<?= $page-1 ?>">Previous</a></li>
    <?php endif; ?>
    <?php for ($i = 1; $i <= $pagInfo['last_page']; $i++): ?>
      <li class="page-item <?= $i === $page ? 'active' : '' ?>">
        <a class="page-link" href="/?page=<?= $i ?>"><?= $i ?></a>
      </li>
    <?php endfor; ?>
    <?php if ($pagInfo['has_next']): ?>
      <li class="page-item"><a class="page-link" href="/?page=<?= $page+1 ?>">Next</a></li>
    <?php endif; ?>
  </ul>
</nav>
<?php endif; ?>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/product.php ------
PHP_FILES["public/product.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Product;
use ShopKit\Cart;
use ShopKit\Csrf;
use ShopKit\Auth;
Auth::startSession();
$slug    = $_GET['slug'] ?? '';
$product = Product::findBySlug($slug);
if (!$product) { http_response_code(404); die('Product not found.'); }
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    Csrf::verify();
    $qty = max(1, (int)($_POST['qty'] ?? 1));
    Cart::add((int)$product['id'], $qty);
    flash_set('success', 'Added to cart!');
    redirect('/product.php?slug=' . urlencode($slug));
}
$pageTitle = $product['name'];
include dirname(__DIR__) . '/templates/layout.php';
?>
<div class="row">
  <div class="col-md-5">
    <?php if ($product['image']): ?>
      <img src="<?= e($product['image']) ?>" class="img-fluid rounded" alt="<?= e($product['name']) ?>">
    <?php else: ?>
      <div class="bg-secondary text-white d-flex align-items-center justify-content-center rounded" style="height:350px">
        <span class="fs-1">&#128722;</span>
      </div>
    <?php endif; ?>
  </div>
  <div class="col-md-7">
    <h1><?= e($product['name']) ?></h1>
    <?php if ($product['category_name']): ?>
      <p><a href="/category.php?slug=<?= urlencode($product['category_slug']) ?>"><?= e($product['category_name']) ?></a></p>
    <?php endif; ?>
    <p class="fs-3 text-success fw-bold"><?= e(setting('shop_currency', 'USD')) ?> <?= number_format((float)$product['price'], 2) ?></p>
    <p><?= nl2br(e($product['description'] ?? '')) ?></p>
    <?php if ($product['stock'] > 0): ?>
      <form method="post">
        <?= Csrf::field() ?>
        <div class="mb-3">
          <label class="form-label">Quantity</label>
          <input type="number" name="qty" value="1" min="1" max="<?= (int)$product['stock'] ?>" class="form-control w-25">
        </div>
        <button type="submit" class="btn btn-primary btn-lg">Add to Cart</button>
      </form>
    <?php else: ?>
      <p class="text-danger fw-bold">Out of Stock</p>
    <?php endif; ?>
  </div>
</div>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/category.php ------
PHP_FILES["public/category.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Product;
use ShopKit\Database;
$slug = $_GET['slug'] ?? '';
$cat  = Database::fetchOne('SELECT * FROM categories WHERE slug = ?', [$slug]);
if (!$cat) { http_response_code(404); die('Category not found.'); }
$page      = max(1, (int)($_GET['page'] ?? 1));
$perPage   = (int)setting('items_per_page', 12);
$products  = Product::listActive($page, $perPage, (int)$cat['id']);
$total     = Product::countActive((int)$cat['id']);
$pagInfo   = paginate($total, $perPage, $page, '/category.php?slug=' . urlencode($slug) . '&page={page}');
$pageTitle = $cat['name'];
include dirname(__DIR__) . '/templates/layout.php';
?>
<h1><?= e($cat['name']) ?></h1>
<?php if ($cat['description']): ?><p><?= e($cat['description']) ?></p><?php endif; ?>
<div class="row row-cols-1 row-cols-md-3 g-4 mt-2">
<?php foreach ($products as $p): ?>
  <div class="col">
    <div class="card h-100 shadow-sm">
      <div class="card-body">
        <h5 class="card-title"><?= e($p['name']) ?></h5>
        <p class="fw-bold text-success"><?= e(setting('shop_currency','USD')) ?> <?= number_format((float)$p['price'],2) ?></p>
      </div>
      <div class="card-footer">
        <a href="/product.php?slug=<?= urlencode($p['slug']) ?>" class="btn btn-primary btn-sm w-100">View</a>
      </div>
    </div>
  </div>
<?php endforeach; ?>
</div>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/cart.php ------
PHP_FILES["public/cart.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Cart;
use ShopKit\Csrf;
use ShopKit\Auth;
Auth::startSession();
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    Csrf::verify();
    $action = $_POST['action'] ?? '';
    $pid    = (int)($_POST['product_id'] ?? 0);
    if ($action === 'update') {
        Cart::update($pid, (int)($_POST['qty'] ?? 0));
    } elseif ($action === 'remove') {
        Cart::remove($pid);
    } elseif ($action === 'clear') {
        Cart::clear();
    }
    redirect('/cart.php');
}
$items     = Cart::getItems();
$total     = Cart::total();
$pageTitle = 'Shopping Cart';
include dirname(__DIR__) . '/templates/layout.php';
?>
<h1>Shopping Cart</h1>
<?php if (empty($items)): ?>
  <p>Your cart is empty. <a href="/">Continue shopping</a>.</p>
<?php else: ?>
  <table class="table table-bordered">
    <thead><tr><th>Product</th><th>Price</th><th>Qty</th><th>Subtotal</th><th></th></tr></thead>
    <tbody>
    <?php foreach ($items as $item): ?>
      <tr>
        <td><a href="/product.php?slug="><?= e($item['name']) ?></a></td>
        <td><?= e(setting('shop_currency','USD')) ?> <?= number_format($item['price'],2) ?></td>
        <td>
          <form method="post" class="d-flex gap-1">
            <?= Csrf::field() ?><input type="hidden" name="action" value="update">
            <input type="hidden" name="product_id" value="<?= (int)$item['product_id'] ?>">
            <input type="number" name="qty" value="<?= (int)$item['qty'] ?>" min="0" class="form-control form-control-sm" style="width:70px">
            <button class="btn btn-sm btn-outline-secondary" type="submit">Update</button>
          </form>
        </td>
        <td><?= e(setting('shop_currency','USD')) ?> <?= number_format($item['price']*$item['qty'],2) ?></td>
        <td>
          <form method="post">
            <?= Csrf::field() ?><input type="hidden" name="action" value="remove">
            <input type="hidden" name="product_id" value="<?= (int)$item['product_id'] ?>">
            <button class="btn btn-sm btn-danger" type="submit">Remove</button>
          </form>
        </td>
      </tr>
    <?php endforeach; ?>
    </tbody>
    <tfoot>
      <tr><td colspan="3" class="text-end fw-bold">Total:</td>
          <td class="fw-bold"><?= e(setting('shop_currency','USD')) ?> <?= number_format($total,2) ?></td><td></td></tr>
    </tfoot>
  </table>
  <div class="d-flex gap-2">
    <form method="post"><<?= Csrf::field() ?><input type="hidden" name="action" value="clear">
      <button class="btn btn-outline-danger" type="submit">Clear Cart</button>
    </form>
    <a href="/checkout.php" class="btn btn-success">Proceed to Checkout</a>
  </div>
<?php endif; ?>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""


# ------ public/checkout.php ------
PHP_FILES["public/checkout.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Cart;
use ShopKit\Csrf;
use ShopKit\Order;
use ShopKit\BTCPayClient;
Auth::startSession();
if (Cart::isEmpty()) {
    flash_set('error', 'Your cart is empty.');
    redirect('/cart.php');
}
$user = Auth::user();
$errors = [];
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    Csrf::verify();
    $name    = trim($_POST['name']    ?? '');
    $email   = trim($_POST['email']   ?? '');
    $address = trim($_POST['address'] ?? '');
    if (!$name)                        $errors[] = 'Name is required.';
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) $errors[] = 'Valid email is required.';
    if (!$errors) {
        $orderId = Order::create(
            ['name' => $name, 'email' => $email, 'address' => $address],
            $user ? (int)$user['id'] : null
        );
        $order = Order::findById($orderId);
        try {
            $btcpay     = new BTCPayClient();
            $proto      = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
            $host       = $_SERVER['HTTP_HOST'] ?? 'localhost';
            $invoiceData = $btcpay->createInvoice(
                (float)$order['total'],
                $order['currency'],
                $orderId,
                $order['order_number'],
                $email,
                "$proto://$host/success.php?order={$order['order_number']}",
                "$proto://$host/webhook.php"
            );
            Order::attachInvoice($orderId, $invoiceData['id'], $invoiceData['checkoutLink']);
            Cart::clear();
            redirect($invoiceData['checkoutLink']);
        } catch (\Throwable $e) {
            $errors[] = 'Payment gateway error: ' . $e->getMessage();
        }
    }
}
$pageTitle = 'Checkout';
include dirname(__DIR__) . '/templates/layout.php';
?>
<h1>Checkout</h1>
<?php foreach ($errors as $err): ?>
  <div class="alert alert-danger"><?= e($err) ?></div>
<?php endforeach; ?>
<div class="row">
  <div class="col-md-7">
    <h5>Your Information</h5>
    <form method="post">
      <?= Csrf::field() ?>
      <div class="mb-3">
        <label class="form-label">Full Name *</label>
        <input type="text" name="name" class="form-control" value="<?= e($user['name'] ?? '') ?>" required>
      </div>
      <div class="mb-3">
        <label class="form-label">Email *</label>
        <input type="email" name="email" class="form-control" value="<?= e($user['email'] ?? '') ?>" required>
      </div>
      <div class="mb-3">
        <label class="form-label">Shipping Address</label>
        <textarea name="address" class="form-control" rows="3"></textarea>
      </div>
      <button type="submit" class="btn btn-success btn-lg">Pay with Bitcoin</button>
    </form>
  </div>
  <div class="col-md-5">
    <h5>Order Summary</h5>
    <table class="table table-sm">
      <?php foreach (Cart::getItems() as $item): ?>
        <tr><td><?= e($item['name']) ?> x<?= (int)$item['qty'] ?></td>
            <td class="text-end"><?= number_format($item['price']*$item['qty'],2) ?></td></tr>
      <?php endforeach; ?>
      <tr class="fw-bold"><td>Total</td><td class="text-end"><?= e(setting('shop_currency','USD')) ?> <?= number_format(Cart::total(),2) ?></td></tr>
    </table>
  </div>
</div>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/webhook.php ------
PHP_FILES["public/webhook.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\BTCPayClient;
use ShopKit\Database;
use ShopKit\Order;
use ShopKit\Config;
use ShopKit\Mailer;
header('Content-Type: application/json');
$rawBody  = file_get_contents('php://input');
$sigHeader = $_SERVER['HTTP_BTCPAY_SIG'] ?? '';
$secret   = Config::get('BTCPAY_WEBHOOK_SECRET', '');
if ($secret && !BTCPayClient::verifyWebhook($rawBody, $sigHeader, $secret)) {
    http_response_code(401);
    echo json_encode(['error' => 'Invalid signature']);
    exit;
}
$payload = json_decode($rawBody, true);
if (!is_array($payload)) {
    http_response_code(400);
    echo json_encode(['error' => 'Bad payload']);
    exit;
}
$type      = $payload['type'] ?? '';
$invoiceId = $payload['invoiceId'] ?? '';
Database::query(
    'INSERT INTO webhook_log (event_type, invoice_id, payload, status) VALUES (?, ?, ?, ?)',
    [$type, $invoiceId, $rawBody, 'received']
);
if (in_array($type, ['InvoiceSettled', 'InvoicePaymentSettled'], true) && $invoiceId) {
    $order = Database::fetchOne(
        'SELECT * FROM orders WHERE btcpay_invoice_id = ?',
        [$invoiceId]
    );
    if ($order && $order['status'] === 'pending') {
        Order::updateStatus((int)$order['id'], 'paid');
        $items = Order::itemsFor((int)$order['id']);
        if (setting('order_email_notifications', '1') === '1') {
            Mailer::orderConfirmation($order, $items);
        }
    }
}
echo json_encode(['ok' => true]);
"""

# ------ public/success.php ------
PHP_FILES["public/success.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Order;
Auth::startSession();
$orderNumber = $_GET['order'] ?? '';
$order       = $orderNumber ? Order::findByOrderNumber($orderNumber) : null;
$pageTitle   = 'Order Confirmed';
include dirname(__DIR__) . '/templates/layout.php';
?>
<div class="text-center py-5">
  <div class="fs-1 mb-3">&#9989;</div>
  <h1>Thank You!</h1>
  <?php if ($order): ?>
    <p class="lead">Your order <strong><?= e($order['order_number']) ?></strong> has been received.</p>
    <p>Status: <span class="badge bg-<?= $order['status']==='paid'?'success':'warning' ?>"><?= e($order['status']) ?></span></p>
    <p class="text-muted">We'll send a confirmation to <?= e($order['customer_email']) ?>.</p>
  <?php else: ?>
    <p class="lead">Your order has been received. Thank you!</p>
  <?php endif; ?>
  <a href="/" class="btn btn-primary mt-3">Continue Shopping</a>
  <?php if (Auth::check()): ?>
    <a href="/account.php" class="btn btn-outline-secondary mt-3">View My Orders</a>
  <?php endif; ?>
</div>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/cancel.php ------
PHP_FILES["public/cancel.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Auth;
Auth::startSession();
$pageTitle = 'Order Cancelled';
include dirname(__DIR__) . '/templates/layout.php';
?>
<div class="text-center py-5">
  <div class="fs-1 mb-3">&#10060;</div>
  <h1>Payment Cancelled</h1>
  <p class="lead">Your payment was cancelled. Your cart items are still saved.</p>
  <a href="/cart.php" class="btn btn-primary mt-3">Return to Cart</a>
</div>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/search.php ------
PHP_FILES["public/search.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Product;
use ShopKit\Auth;
Auth::startSession();
$q        = trim($_GET['q'] ?? '');
$page     = max(1, (int)($_GET['page'] ?? 1));
$perPage  = (int)setting('items_per_page', 12);
$products = $q ? Product::search($q, $page, $perPage) : [];
$pageTitle = 'Search' . ($q ? ': ' . $q : '');
include dirname(__DIR__) . '/templates/layout.php';
?>
<h1>Search</h1>
<form method="get" class="mb-4">
  <div class="input-group">
    <input type="search" name="q" class="form-control" placeholder="Search products..." value="<?= e($q) ?>">
    <button class="btn btn-primary" type="submit">Search</button>
  </div>
</form>
<?php if ($q): ?>
  <p><?= count($products) ?> result(s) for "<?= e($q) ?>"</p>
  <div class="row row-cols-1 row-cols-md-3 g-4">
    <?php foreach ($products as $p): ?>
      <div class="col">
        <div class="card h-100 shadow-sm">
          <div class="card-body">
            <h5 class="card-title"><?= e($p['name']) ?></h5>
            <p class="fw-bold text-success"><?= e(setting('shop_currency','USD')) ?> <?= number_format((float)$p['price'],2) ?></p>
          </div>
          <div class="card-footer">
            <a href="/product.php?slug=<?= urlencode($p['slug']) ?>" class="btn btn-primary btn-sm w-100">View</a>
          </div>
        </div>
      </div>
    <?php endforeach; ?>
  </div>
<?php endif; ?>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/sitemap.php ------
PHP_FILES["public/sitemap.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Sitemap;
$proto  = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
$host   = $_SERVER['HTTP_HOST'] ?? 'localhost';
$base   = "$proto://$host";
header('Content-Type: application/xml; charset=UTF-8');
echo Sitemap::generate($base);
"""

# ------ public/robots.txt ------
PHP_FILES["public/robots.txt"] = r"""User-agent: *
Disallow: /admin/
Disallow: /checkout.php
Disallow: /account.php
Sitemap: __SITEMAP_URL__
"""


# ------ public/login.php ------
PHP_FILES["public/login.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Csrf;
Auth::startSession();
if (Auth::check()) { redirect('/account.php'); }
$errors = [];
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    Csrf::verify();
    $email    = trim($_POST['email']    ?? '');
    $password = $_POST['password'] ?? '';
    if (Auth::login($email, $password)) {
        $redirect = $_GET['redirect'] ?? '/account.php';
        redirect(filter_var($redirect, FILTER_VALIDATE_URL) ? '/' : $redirect);
    } else {
        $errors[] = 'Invalid email or password.';
    }
}
$pageTitle = 'Login';
include dirname(__DIR__) . '/templates/layout.php';
?>
<div class="row justify-content-center">
  <div class="col-md-5">
    <div class="card shadow-sm">
      <div class="card-body">
        <h2 class="card-title mb-4">Sign In</h2>
        <?php foreach ($errors as $err): ?>
          <div class="alert alert-danger"><?= e($err) ?></div>
        <?php endforeach; ?>
        <form method="post">
          <?= Csrf::field() ?>
          <div class="mb-3">
            <label class="form-label">Email</label>
            <input type="email" name="email" class="form-control" required autofocus>
          </div>
          <div class="mb-3">
            <label class="form-label">Password</label>
            <input type="password" name="password" class="form-control" required>
          </div>
          <button type="submit" class="btn btn-primary w-100">Login</button>
        </form>
        <p class="text-center mt-3">No account? <a href="/register.php">Register</a></p>
      </div>
    </div>
  </div>
</div>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/register.php ------
PHP_FILES["public/register.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Csrf;
Auth::startSession();
if (Auth::check()) { redirect('/account.php'); }
if (setting('allow_registration', '1') !== '1') {
    die('Registration is currently disabled.');
}
$errors = [];
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    Csrf::verify();
    $name     = trim($_POST['name']     ?? '');
    $email    = trim($_POST['email']    ?? '');
    $password = $_POST['password'] ?? '';
    $confirm  = $_POST['confirm']  ?? '';
    if (strlen($name) < 2)                         $errors[] = 'Name must be at least 2 characters.';
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) $errors[] = 'Valid email required.';
    if (strlen($password) < 8)                     $errors[] = 'Password must be at least 8 characters.';
    if ($password !== $confirm)                    $errors[] = 'Passwords do not match.';
    if (!$errors) {
        try {
            $userId = Auth::register($name, $email, $password);
            Auth::login($email, $password);
            flash_set('success', 'Account created. Welcome!');
            redirect('/account.php');
        } catch (\Throwable $e) {
            $errors[] = $e->getMessage();
        }
    }
}
$pageTitle = 'Register';
include dirname(__DIR__) . '/templates/layout.php';
?>
<div class="row justify-content-center">
  <div class="col-md-5">
    <div class="card shadow-sm">
      <div class="card-body">
        <h2 class="card-title mb-4">Create Account</h2>
        <?php foreach ($errors as $err): ?>
          <div class="alert alert-danger"><?= e($err) ?></div>
        <?php endforeach; ?>
        <form method="post">
          <?= Csrf::field() ?>
          <div class="mb-3"><label class="form-label">Name</label>
            <input type="text" name="name" class="form-control" required autofocus></div>
          <div class="mb-3"><label class="form-label">Email</label>
            <input type="email" name="email" class="form-control" required></div>
          <div class="mb-3"><label class="form-label">Password</label>
            <input type="password" name="password" class="form-control" required minlength="8"></div>
          <div class="mb-3"><label class="form-label">Confirm Password</label>
            <input type="password" name="confirm" class="form-control" required minlength="8"></div>
          <button type="submit" class="btn btn-success w-100">Register</button>
        </form>
        <p class="text-center mt-3">Already have an account? <a href="/login.php">Login</a></p>
      </div>
    </div>
  </div>
</div>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""

# ------ public/logout.php ------
PHP_FILES["public/logout.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Auth;
Auth::startSession();
Auth::logout();
redirect('/login.php');
"""

# ------ public/account.php ------
PHP_FILES["public/account.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(__DIR__) . '/vendor/autoload.php';
require_once dirname(__DIR__) . '/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Order;
Auth::startSession();
$user      = Auth::requireLogin();
$orders    = Order::listForUser((int)$user['id']);
$pageTitle = 'My Account';
include dirname(__DIR__) . '/templates/layout.php';
?>
<h1>My Account</h1>
<p>Welcome, <?= e($user['name']) ?>! (<a href="/logout.php">Logout</a>)</p>
<h4 class="mt-4">My Orders</h4>
<?php if (empty($orders)): ?>
  <p>No orders yet. <a href="/">Start shopping</a>.</p>
<?php else: ?>
  <table class="table table-striped table-hover">
    <thead><tr><th>Order #</th><th>Date</th><th>Total</th><th>Status</th><th>Payment</th></tr></thead>
    <tbody>
    <?php foreach ($orders as $ord): ?>
      <tr>
        <td><?= e($ord['order_number']) ?></td>
        <td><?= e($ord['created_at']) ?></td>
        <td><?= e($ord['currency']) ?> <?= number_format((float)$ord['total'],2) ?></td>
        <td><span class="badge bg-<?= $ord['status']==='paid'?'success':($ord['status']==='pending'?'warning':'secondary') ?>">
          <?= e($ord['status']) ?></span></td>
        <td>
          <?php if ($ord['btcpay_invoice_url'] && $ord['status']==='pending'): ?>
            <a href="<?= e($ord['btcpay_invoice_url']) ?>" class="btn btn-sm btn-warning" target="_blank">Pay Now</a>
          <?php else: ?>
            Bitcoin
          <?php endif; ?>
        </td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table>
<?php endif; ?>
<?php include dirname(__DIR__) . '/templates/footer.php'; ?>
"""


# ------ public/admin/index.php ------
PHP_FILES["public/admin/index.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(dirname(__DIR__)) . '/vendor/autoload.php';
require_once dirname(dirname(__DIR__)) . '/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Database;
use ShopKit\Order;
Auth::startSession();
$user = Auth::requireAdmin();
$totalOrders   = Order::countAll();
$totalRevenue  = Database::fetchOne("SELECT COALESCE(SUM(total),0) AS r FROM orders WHERE status='paid'")['r'] ?? 0;
$totalProducts = Database::fetchOne('SELECT COUNT(*) AS n FROM products WHERE is_active=1')['n'] ?? 0;
$totalUsers    = Database::fetchOne('SELECT COUNT(*) AS n FROM users WHERE is_admin=0')['n'] ?? 0;
$recentOrders  = Order::listAll(1, 10);
$pageTitle = 'Admin Dashboard';
include dirname(dirname(__DIR__)) . '/templates/layout.php';
?>
<h1>Admin Dashboard</h1>
<div class="row g-4 mb-4">
  <div class="col-md-3"><div class="card text-white bg-primary"><div class="card-body">
    <div class="fs-2 fw-bold"><?= (int)$totalOrders ?></div><div>Total Orders</div>
  </div></div></div>
  <div class="col-md-3"><div class="card text-white bg-success"><div class="card-body">
    <div class="fs-2 fw-bold">$<?= number_format((float)$totalRevenue,2) ?></div><div>Revenue (Paid)</div>
  </div></div></div>
  <div class="col-md-3"><div class="card text-white bg-info"><div class="card-body">
    <div class="fs-2 fw-bold"><?= (int)$totalProducts ?></div><div>Active Products</div>
  </div></div></div>
  <div class="col-md-3"><div class="card text-white bg-secondary"><div class="card-body">
    <div class="fs-2 fw-bold"><?= (int)$totalUsers ?></div><div>Customers</div>
  </div></div></div>
</div>
<h4>Recent Orders</h4>
<table class="table table-striped"><thead><tr><th>Order #</th><th>Customer</th><th>Total</th><th>Status</th><th>Date</th></tr></thead><tbody>
<?php foreach ($recentOrders as $o): ?>
<tr><td><?= e($o['order_number']) ?></td><td><?= e($o['customer_name']) ?></td>
    <td><?= e($o['currency']) ?> <?= number_format((float)$o['total'],2) ?></td>
    <td><span class="badge bg-<?= $o['status']==='paid'?'success':'warning' ?>"><?= e($o['status']) ?></span></td>
    <td><?= e($o['created_at']) ?></td></tr>
<?php endforeach; ?>
</tbody></table>
<?php include dirname(dirname(__DIR__)) . '/templates/footer.php'; ?>
"""

PHP_FILES["public/admin/products.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(dirname(__DIR__)) . '/vendor/autoload.php';
require_once dirname(dirname(__DIR__)) . '/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Database;
use ShopKit\Csrf;
Auth::startSession();
Auth::requireAdmin();
$errors=[];$success='';
if ($_SERVER['REQUEST_METHOD']==='POST') {
    Csrf::verify();
    $action=(string)($_POST['action']??'');
    if ($action==='delete') {
        Database::query('UPDATE products SET is_active=0 WHERE id=?',[(int)$_POST['id']]);
        $success='Product deactivated.';
    } elseif ($action==='save') {
        $id=(int)($_POST['id']??0);
        $name=trim($_POST['name']??''); $price=(float)($_POST['price']??0);
        $stock=(int)($_POST['stock']??0); $desc=trim($_POST['description']??'');
        $catId=$_POST['category_id']??(int)0;
        if(!$name) $errors[]='Name required.';
        if(!$errors){
            if($id){
                Database::query('UPDATE products SET name=?,price=?,stock=?,description=?,category_id=? WHERE id=?',
                    [$name,$price,$stock,$desc,$catId,$id]);
            } else {
                $slug=slugify($name);
                Database::query('INSERT INTO products (name,slug,price,stock,description,category_id) VALUES (?,?,?,?,?,?)',
                    [$name,$slug,$price,$stock,$desc,$catId]);
            }
            $success='Saved.';
        }
    }
}
$products=Database::fetchAll('SELECT p.*,c.name AS cat FROM products p LEFT JOIN categories c ON c.id=p.category_id ORDER BY p.id DESC');
$categories=Database::fetchAll('SELECT * FROM categories ORDER BY name');
$pageTitle='Admin: Products';
include dirname(dirname(__DIR__)).'/templates/layout.php';
?>
<h1>Products</h1>
<?php if($success): ?><div class="alert alert-success"><?= e($success) ?></div><?php endif; ?>
<?php foreach($errors as $err): ?><div class="alert alert-danger"><?= e($err) ?></div><?php endforeach; ?>
<table class="table table-sm table-hover"><thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Category</th><th>Active</th><th></th></tr></thead><tbody>
<?php foreach($products as $p): ?>
<tr><td><?= (int)$p['id'] ?></td><td><?= e($p['name']) ?></td>
<td><?= number_format((float)$p['price'],2) ?></td><td><?= (int)$p['stock'] ?></td>
<td><?= e($p['cat']??'-') ?></td><td><?= $p['is_active']?'Yes':'No' ?></td>
<td>
  <form method="post" class="d-inline"><?= Csrf::field() ?>
    <input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="<?= (int)$p['id'] ?>">
    <button class="btn btn-sm btn-danger" onclick="return confirm('Deactivate?')">Del</button></form>
</td></tr>
<?php endforeach; ?>
</tbody></table>
<?php include dirname(dirname(__DIR__)).'/templates/footer.php'; ?>
"""

PHP_FILES["public/admin/orders.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(dirname(__DIR__)).'/vendor/autoload.php';
require_once dirname(dirname(__DIR__)).'/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Order;
use ShopKit\Csrf;
Auth::startSession();
Auth::requireAdmin();
if ($_SERVER['REQUEST_METHOD']==='POST') {
    Csrf::verify();
    Order::updateStatus((int)$_POST['id'], $_POST['status']);
    flash_set('success','Order status updated.');
    redirect('/admin/orders.php');
}
$page=max(1,(int)($_GET['page']??1));
$orders=Order::listAll($page,20);
$total=Order::countAll();
$pageTitle='Admin: Orders';
include dirname(dirname(__DIR__)).'/templates/layout.php';
?>
<h1>Orders</h1>
<table class="table table-sm table-hover"><thead><tr><th>Order #</th><th>Customer</th><th>Total</th><th>Status</th><th>Date</th><th></th></tr></thead><tbody>
<?php foreach($orders as $o): ?>
<tr><td><?= e($o['order_number']) ?></td><td><?= e($o['customer_name']) ?><br><small><?= e($o['customer_email']) ?></small></td>
<td><?= e($o['currency']) ?> <?= number_format((float)$o['total'],2) ?></td>
<td><span class="badge bg-<?= $o['status']==='paid'?'success':'warning' ?>"><?= e($o['status']) ?></span></td>
<td><?= e($o['created_at']) ?></td>
<td><form method="post" class="d-flex gap-1"><?= Csrf::field() ?>
  <input type="hidden" name="id" value="<?= (int)$o['id'] ?>">
  <select name="status" class="form-select form-select-sm">
    <?php foreach(['pending','paid','processing','shipped','completed','cancelled','refunded'] as $s): ?>
      <option value="<?= $s ?>" <?= $o['status']===$s?'selected':'' ?>><?= $s ?></option>
    <?php endforeach; ?>
  </select>
  <button class="btn btn-sm btn-primary">Save</button>
</form></td></tr>
<?php endforeach; ?>
</tbody></table>
<?php include dirname(dirname(__DIR__)).'/templates/footer.php'; ?>
"""

PHP_FILES["public/admin/settings.php"] = r"""<?php
declare(strict_types=1);
require_once dirname(dirname(__DIR__)).'/vendor/autoload.php';
require_once dirname(dirname(__DIR__)).'/src/helpers.php';
use ShopKit\Auth;
use ShopKit\Database;
use ShopKit\Csrf;
Auth::startSession();
Auth::requireAdmin();
if ($_SERVER['REQUEST_METHOD']==='POST') {
    Csrf::verify();
    $keys=['shop_name','shop_currency','items_per_page','maintenance_mode','allow_registration','order_email_notifications'];
    foreach ($keys as $k) {
        $v = $_POST[$k] ?? '0';
        Database::query("INSERT INTO settings (`key`,`value`) VALUES (?,?) ON DUPLICATE KEY UPDATE `value`=?",[$k,$v,$v]);
    }
    flash_set('success','Settings saved.');
    redirect('/admin/settings.php');
}
$pageTitle='Admin: Settings';
include dirname(dirname(__DIR__)).'/templates/layout.php';
?>
<h1>Settings</h1>
<form method="post" class="col-md-6">
  <?= Csrf::field() ?>
  <div class="mb-3"><label class="form-label">Shop Name</label>
    <input type="text" name="shop_name" class="form-control" value="<?= e(setting('shop_name')) ?>"></div>
  <div class="mb-3"><label class="form-label">Currency</label>
    <input type="text" name="shop_currency" class="form-control" value="<?= e(setting('shop_currency','USD')) ?>"></div>
  <div class="mb-3"><label class="form-label">Items Per Page</label>
    <input type="number" name="items_per_page" class="form-control" value="<?= e(setting('items_per_page','12')) ?>"></div>
  <div class="mb-3 form-check">
    <input type="hidden" name="maintenance_mode" value="0">
    <input class="form-check-input" type="checkbox" name="maintenance_mode" value="1" <?= setting('maintenance_mode')==='1'?'checked':'' ?>>
    <label class="form-check-label">Maintenance Mode</label></div>
  <div class="mb-3 form-check">
    <input type="hidden" name="allow_registration" value="0">
    <input class="form-check-input" type="checkbox" name="allow_registration" value="1" <?= setting('allow_registration','1')==='1'?'checked':'' ?>>
    <label class="form-check-label">Allow Registration</label></div>
  <div class="mb-3 form-check">
    <input type="hidden" name="order_email_notifications" value="0">
    <input class="form-check-input" type="checkbox" name="order_email_notifications" value="1" <?= setting('order_email_notifications','1')==='1'?'checked':'' ?>>
    <label class="form-check-label">Order Email Notifications</label></div>
  <button type="submit" class="btn btn-primary">Save Settings</button>
</form>
<?php include dirname(dirname(__DIR__)).'/templates/footer.php'; ?>
"""

# ------ autoload stub ------
PHP_FILES["vendor/autoload.php"] = r"""<?php
declare(strict_types=1);
spl_autoload_register(function (string $class): void {
    $base  = dirname(__DIR__) . '/src/';
    $parts = explode('\\', $class);
    if (array_shift($parts) !== 'ShopKit') return;
    $file = $base . implode('/', $parts) . '.php';
    if (is_file($file)) require_once $file;
});
require_once dirname(__DIR__) . '/src/helpers.php';
\ShopKit\Config::load();
"""


# ---------------------------------------------------------------------------
# CONFIG TEMPLATES
# ---------------------------------------------------------------------------
NGINX_CONFIG = """\
server {{
    listen 80;
    server_name {domain};
    root /var/www/shopkit/public;
    index index.php;
    charset utf-8;

    location = /robots.txt {{ access_log off; }}
    location ~* \\.php$ {{
        fastcgi_pass unix:{php_sock};
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include fastcgi_params;
    }}
    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {{
        expires 30d;
        access_log off;
    }}
    location / {{ try_files $uri $uri/ /index.php$is_args$args; }}
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
}}
"""

NGINX_HTTPS_BLOCK = """\
    listen 443 ssl http2;
    ssl_certificate     {ssl_cert};
    ssl_certificate_key {ssl_key};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
"""

SYSTEMD_SERVICE = """\
[Unit]
Description=ShopKit Watchdog
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/shopkit/shopkit.py watchdog
StandardOutput=append:/var/log/shopkit/watchdog.log
StandardError=append:/var/log/shopkit/watchdog.log

[Install]
WantedBy=multi-user.target
"""

SYSTEMD_TIMER = """\
[Unit]
Description=ShopKit Watchdog Timer

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Unit=shopkit-watchdog.service

[Install]
WantedBy=timers.target
"""

LOGROTATE_CONFIG = """\
/var/log/shopkit/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 www-data adm
    sharedscripts
    postrotate
        /usr/bin/find /var/log/shopkit -name "*.log" -empty -delete
    endscript
}
"""

APP_CSS = """\
body { font-family: 'Segoe UI', system-ui, sans-serif; }
.card { border-radius: 0.75rem; }
.navbar-brand { letter-spacing: 0.05em; }
footer { border-top: 1px solid rgba(255,255,255,.1); }
"""

APP_JS = """\
'use strict';
document.addEventListener('DOMContentLoaded', () => {
  // Auto-dismiss alerts after 5s
  document.querySelectorAll('.alert.alert-dismissible').forEach(el => {
    setTimeout(() => { const b = bootstrap.Alert.getOrCreateInstance(el); b && b.close(); }, 5000);
  });
});
"""

# ---------------------------------------------------------------------------
# PYTHON HELPERS
# ---------------------------------------------------------------------------
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

def setup_logging(verbose: bool = False) -> None:
    """Configure root logger."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_cmd(args: list[str], *, check: bool = True, input: str | None = None,
            capture: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command, logging it first."""
    logger.debug("run: %s", " ".join(str(a) for a in args))
    return subprocess.run(
        args,
        check=check,
        input=input,
        text=True,
        capture_output=capture,
    )


def ensure_root() -> None:
    """Abort if not running as root."""
    if os.geteuid() != 0:
        logger.error("shopkit must be run as root (sudo).")
        sys.exit(1)


def detect_php_sock() -> str:
    """Return the PHP-FPM socket path for installed PHP version."""
    for ver in ("8.3", "8.2", "8.1"):
        p = Path(f"/var/run/php/php{ver}-fpm.sock")
        if p.exists():
            return str(p)
    return "/var/run/php/php8.2-fpm.sock"


def get_public_ip() -> str:
    """Return the server's public IPv4 address."""
    try:
        import urllib.request
        return urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode().strip()
    except Exception:
        return "127.0.0.1"


def domain_resolves_to_self(domain: str) -> bool:
    """Check if domain A-record matches this server's IP."""
    try:
        resolved = socket.gethostbyname(domain)
        my_ip = get_public_ip()
        return resolved == my_ip
    except Exception:
        return False


def ensure_python_deps() -> None:
    """Install pinned Python dependencies."""
    logger.info("Installing Python dependencies...")
    run_cmd([sys.executable, "-m", "pip", "install", "--quiet"] + PINNED_DEPS)


def write_file(path: Path, content: str, mode: int = 0o644) -> None:
    """Write content to path, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)
    logger.debug("wrote %s", path)


def load_env(env_file: Path) -> dict[str, str]:
    """Load key=value pairs from env file."""
    env: dict[str, str] = {}
    if not env_file.exists():
        return env
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def write_env_files(config: dict[str, str]) -> None:
    """Write secrets to /etc/shopkit/shopkit.env (0600)."""
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in config.items()]
    content = "\n".join(lines) + "\n"
    write_file(ENV_FILE, content, mode=0o600)
    logger.info("Env written to %s", ENV_FILE)


# ---------------------------------------------------------------------------
# INSTALL STEPS
# ---------------------------------------------------------------------------

def install_system_packages() -> None:
    """Install nginx, php, mysql and required extensions."""
    logger.info("Updating apt and installing packages...")
    run_cmd(["apt-get", "update", "-qq"])
    pkgs = [
        "nginx", "mysql-server", "php8.2-fpm", "php8.2-mysql", "php8.2-curl",
        "php8.2-mbstring", "php8.2-xml", "php8.2-zip", "php8.2-intl",
        "certbot", "python3-certbot-nginx", "ufw", "fail2ban",
        "openssl", "curl", "unzip",
    ]
    run_cmd(["apt-get", "install", "-y", "--no-install-recommends"] + pkgs)
    logger.info("Packages installed.")


def setup_mysql(db_name: str, db_user: str, db_pass: str) -> None:
    """Create MySQL database and user."""
    logger.info("Configuring MySQL...")
    sql = (
        f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}';\n"
        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost';\n"
        f"FLUSH PRIVILEGES;\n"
    )
    run_cmd(["mysql", "--execute=" + sql])
    logger.info("MySQL configured.")


def run_schema(db_name: str, db_user: str, db_pass: str) -> None:
    """Import the SQL schema."""
    logger.info("Running database schema...")
    run_cmd(
        ["mysql", f"-u{db_user}", f"-p{db_pass}", db_name],
        input=SQL_SCHEMA,
    )
    logger.info("Schema imported.")


def setup_directories() -> None:
    """Create application directory structure."""
    dirs = [
        BASE_DIR / "src",
        BASE_DIR / "public" / "admin",
        BASE_DIR / "public" / "assets" / "css",
        BASE_DIR / "public" / "assets" / "js",
        BASE_DIR / "templates",
        BASE_DIR / "vendor",
        LOG_DIR,
        BACKUP_DIR,
        ENV_DIR,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    run_cmd(["chown", "-R", "www-data:www-data", str(BASE_DIR)])
    run_cmd(["chmod", "-R", "750", str(BASE_DIR)])
    logger.info("Directories created.")


def write_php_files() -> None:
    """Write all PHP source files to disk."""
    logger.info("Writing PHP files...")
    for rel, content in PHP_FILES.items():
        write_file(BASE_DIR / rel, content)
    run_cmd(["chown", "-R", "www-data:www-data", str(BASE_DIR)])
    logger.info("PHP files written.")


def write_static_files() -> None:
    """Write CSS and JS static files."""
    write_file(BASE_DIR / "public" / "assets" / "css" / "app.css", APP_CSS)
    write_file(BASE_DIR / "public" / "assets" / "js" / "app.js", APP_JS)
    logger.info("Static files written.")


def _verify_bootstrap(path: Path, expected_sha: str) -> None:
    content = path.read_bytes()
    digest = base64.b64encode(hashlib.sha384(content).digest()).decode()
    expected = expected_sha.split("-", 1)[1]
    if not _hmac.compare_digest(digest, expected):
        raise RuntimeError(f"Bootstrap SHA-384 mismatch for {path.name}")


def download_bootstrap() -> None:
    """Download and verify Bootstrap 5.3 assets."""
    import urllib.request
    logger.info("Downloading Bootstrap 5.3...")
    css_path = BASE_DIR / "public" / "assets" / "css" / "bootstrap.min.css"
    js_path  = BASE_DIR / "public" / "assets" / "js"  / "bootstrap.bundle.min.js"
    for url, path in [(BOOTSTRAP_CSS_URL, css_path), (BOOTSTRAP_JS_URL, js_path)]:
        urllib.request.urlretrieve(url, path)
    _verify_bootstrap(css_path, BOOTSTRAP_CSS_SHA)
    _verify_bootstrap(js_path,  BOOTSTRAP_JS_SHA)
    logger.info("Bootstrap verified and saved.")


def create_admin_user(email: str, password: str, name: str,
                      db_name: str, db_user: str, db_pass: str) -> None:
    """Create admin user via PHP CLI."""
    logger.info("Creating admin user...")
    result = run_cmd(
        ["php", "-r", f"echo password_hash('{password}', PASSWORD_ARGON2ID);"],
        capture=True,
    )
    phash = result.stdout.strip()
    sql = (
        f"INSERT INTO users (name, email, password_hash, is_admin) "
        f"VALUES ('{name}', '{email}', '{phash}', 1) "
        f"ON DUPLICATE KEY UPDATE password_hash='{phash}', is_admin=1;\n"
    )
    run_cmd(["mysql", f"-u{db_user}", f"-p{db_pass}", db_name], input=sql)
    logger.info("Admin user created: %s", email)


def setup_nginx(domain: str, php_sock: str, tls: bool = False,
                ssl_cert: str = "", ssl_key: str = "") -> None:
    """Write nginx site config."""
    logger.info("Configuring nginx for %s...", domain)
    cfg = NGINX_CONFIG.format(domain=domain, php_sock=php_sock)
    if tls and ssl_cert and ssl_key:
        https_block = NGINX_HTTPS_BLOCK.format(ssl_cert=ssl_cert, ssl_key=ssl_key)
        cfg = cfg.replace("    listen 80;", https_block + "    listen 80;\n    return 301 https://$host$request_uri;")
    write_file(NGINX_CONF, cfg)
    if not NGINX_ENABLED.exists():
        NGINX_ENABLED.symlink_to(NGINX_CONF)
    run_cmd(["nginx", "-t"])
    run_cmd(["systemctl", "reload", "nginx"])
    logger.info("Nginx configured.")


def setup_tls(domain: str, email: str) -> tuple[str, str]:
    """Run certbot to obtain TLS certificate."""
    logger.info("Obtaining TLS certificate for %s...", domain)
    run_cmd([
        "certbot", "--nginx", "--non-interactive", "--agree-tos",
        "-m", email, "-d", domain,
    ])
    cert = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
    key  = f"/etc/letsencrypt/live/{domain}/privkey.pem"
    return cert, key


def setup_ufw(domain: str) -> None:
    """Configure UFW firewall rules."""
    logger.info("Configuring UFW firewall...")
    for cmd in [
        ["ufw", "--force", "reset"],
        ["ufw", "default", "deny", "incoming"],
        ["ufw", "default", "allow", "outgoing"],
        ["ufw", "allow", "ssh"],
        ["ufw", "allow", "80/tcp"],
        ["ufw", "allow", "443/tcp"],
        ["ufw", "--force", "enable"],
    ]:
        run_cmd(cmd)
    logger.info("UFW configured.")


def setup_fail2ban() -> None:
    """Write fail2ban jail for nginx."""
    logger.info("Configuring fail2ban...")
    jail = """\
[nginx-http-auth]
enabled  = true
port     = http,https
filter   = nginx-http-auth
logpath  = /var/log/nginx/error.log
maxretry = 5
bantime  = 3600

[nginx-limit-req]
enabled  = true
port     = http,https
filter   = nginx-limit-req
logpath  = /var/log/nginx/error.log
maxretry = 10
bantime  = 600
"""
    write_file(Path("/etc/fail2ban/jail.d/shopkit.conf"), jail)
    run_cmd(["systemctl", "restart", "fail2ban"])
    logger.info("fail2ban configured.")


def setup_systemd() -> None:
    """Install and enable the watchdog systemd service."""
    write_file(SYSTEMD_SERVICE, SYSTEMD_SERVICE)
    write_file(SYSTEMD_TIMER,   SYSTEMD_TIMER)
    run_cmd(["systemctl", "daemon-reload"])
    run_cmd(["systemctl", "enable", "--now", "shopkit-watchdog.timer"])
    logger.info("Systemd watchdog timer enabled.")


def setup_logrotate() -> None:
    """Install logrotate config."""
    write_file(LOGROTATE_CONF, LOGROTATE_CONFIG)
    logger.info("Logrotate configured.")


# ---------------------------------------------------------------------------
# WIZARD
# ---------------------------------------------------------------------------

def run_wizard() -> dict[str, str]:
    """Interactive setup wizard. Returns config dict."""
    print("\n=== ShopKit Setup Wizard ===\n")

    def ask(prompt: str, default: str = "", secret: bool = False) -> str:
        import getpass
        disp = f" [{default}]" if default else ""
        if secret:
            val = getpass.getpass(f"{prompt}{disp}: ") or default
        else:
            val = input(f"{prompt}{disp}: ").strip() or default
        return val

    domain      = ask("Domain name (e.g. shop.example.com)")
    admin_email = ask("Admin email")
    admin_name  = ask("Admin name", "Admin")
    admin_pass  = ask("Admin password (min 12 chars)", secret=True)
    shop_name   = ask("Shop name", "My Shop")
    currency    = ask("Currency", "USD")
    btcpay_host = ask("BTCPay Server URL (e.g. https://btcpay.example.com)")
    btcpay_key  = ask("BTCPay API key", secret=True)
    btcpay_store = ask("BTCPay Store ID")
    db_name     = ask("MySQL database name", "shopkit")
    db_user     = ask("MySQL username", "shopkit")
    db_pass     = ask("MySQL password", secrets.token_urlsafe(16), secret=True)
    webhook_sec = secrets.token_hex(32)
    tls         = ask("Enable TLS with Let's Encrypt? [y/N]", "N").lower() == "y"

    return {
        "DOMAIN":                domain,
        "ADMIN_EMAIL":           admin_email,
        "ADMIN_NAME":            admin_name,
        "ADMIN_PASS":            admin_pass,
        "SHOP_NAME":             shop_name,
        "SHOP_CURRENCY":         currency,
        "SHOP_DOMAIN":           domain,
        "BTCPAY_HOST":           btcpay_host,
        "BTCPAY_API_KEY":        btcpay_key,
        "BTCPAY_STORE_ID":       btcpay_store,
        "BTCPAY_WEBHOOK_SECRET": webhook_sec,
        "DB_HOST":               "127.0.0.1",
        "DB_PORT":               "3306",
        "DB_NAME":               db_name,
        "DB_USER":               db_user,
        "DB_PASS":               db_pass,
        "ENABLE_TLS":            "1" if tls else "0",
    }


# ---------------------------------------------------------------------------
# COMMAND HANDLERS
# ---------------------------------------------------------------------------

def cmd_install(args: argparse.Namespace) -> None:
    """Full installation."""
    ensure_root()
    setup_logging(getattr(args, "verbose", False))

    if getattr(args, "config", None) and Path(args.config).exists():
        cfg = load_env(Path(args.config))
    else:
        cfg = run_wizard()

    domain      = cfg["DOMAIN"]
    db_name     = cfg["DB_NAME"]
    db_user     = cfg["DB_USER"]
    db_pass     = cfg["DB_PASS"]
    admin_email = cfg["ADMIN_EMAIL"]
    admin_name  = cfg.get("ADMIN_NAME", "Admin")
    admin_pass  = cfg["ADMIN_PASS"]
    tls         = cfg.get("ENABLE_TLS", "0") == "1"

    logger.info("Installing ShopKit %s on %s...", SHOPKIT_VERSION, domain)

    ensure_python_deps()
    install_system_packages()
    setup_directories()
    write_php_files()
    write_static_files()
    download_bootstrap()
    setup_mysql(db_name, db_user, db_pass)
    run_schema(db_name, db_user, db_pass)
    create_admin_user(admin_email, admin_pass, admin_name, db_name, db_user, db_pass)
    write_env_files(cfg)

    php_sock = detect_php_sock()
    ssl_cert = ssl_key = ""
    if tls:
        if domain_resolves_to_self(domain):
            setup_nginx(domain, php_sock)
            ssl_cert, ssl_key = setup_tls(domain, admin_email)
            setup_nginx(domain, php_sock, tls=True, ssl_cert=ssl_cert, ssl_key=ssl_key)
        else:
            logger.warning("Domain %s does not resolve to this server. Skipping TLS.", domain)
    else:
        setup_nginx(domain, php_sock)

    setup_ufw(domain)
    setup_fail2ban()
    setup_systemd()
    setup_logrotate()

    print("\n" + "="*60)
    print(f"  ShopKit installed successfully!")
    print(f"  URL:        http{'s' if tls else ''}://{domain}/")
    print(f"  Admin:      http{'s' if tls else ''}://{domain}/admin/")
    print(f"  Admin user: {admin_email}")
    print("="*60 + "\n")


def cmd_wizard(args: argparse.Namespace) -> None:
    """Run wizard only, output env file."""
    cfg = run_wizard()
    out = Path(getattr(args, "output", "shopkit.env"))
    write_file(out, "\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n")
    print(f"Config written to {out}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show service status."""
    for svc in ["nginx", "mysql", "fail2ban"]:
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
        status = r.stdout.strip()
        print(f"  {svc:<15} {status}")
    php_sock = detect_php_sock()
    ver = php_sock.split("php")[1].split("-")[0] if "php" in php_sock else "?"
    r = subprocess.run(["systemctl", "is-active", f"php{ver}-fpm"], capture_output=True, text=True)
    print(f"  {'php-fpm':<15} {r.stdout.strip()}")


def cmd_backup(args: argparse.Namespace) -> None:
    """Backup database and files."""
    ensure_root()
    cfg      = load_env(ENV_FILE)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_file  = BACKUP_DIR / f"db_{ts}.sql.gz"
    enc_file = BACKUP_DIR / f"db_{ts}.sql.gz.enc"
    key      = cfg.get("BACKUP_KEY") or cfg.get("DB_PASS", "shopkit")
    logger.info("Backing up database...")
    with open(db_file, "wb") as fh:
        dump = subprocess.Popen(
            ["mysqldump", f"-u{cfg['DB_USER']}", f"-p{cfg['DB_PASS']}", cfg["DB_NAME"]],
            stdout=subprocess.PIPE,
        )
        gzip_proc = subprocess.Popen(["gzip"], stdin=dump.stdout, stdout=fh)
        dump.stdout.close()
        gzip_proc.wait()
        dump.wait()
    run_cmd([
        "openssl", "enc", "-aes-256-cbc", "-pbkdf2",
        "-pass", f"pass:{key}",
        "-in", str(db_file), "-out", str(enc_file),
    ])
    db_file.unlink()
    print(f"Backup saved: {enc_file}")


def cmd_restore(args: argparse.Namespace) -> None:
    """Restore from encrypted backup."""
    ensure_root()
    cfg  = load_env(ENV_FILE)
    path = Path(args.file)
    key  = cfg.get("BACKUP_KEY") or cfg.get("DB_PASS", "shopkit")
    dec  = path.with_suffix("")
    run_cmd([
        "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
        "-pass", f"pass:{key}",
        "-in", str(path), "-out", str(dec),
    ])
    with open(dec, "rb") as fh:
        gz = subprocess.Popen(["gzip", "-d"], stdin=fh, stdout=subprocess.PIPE)
        subprocess.run(
            ["mysql", f"-u{cfg['DB_USER']}", f"-p{cfg['DB_PASS']}", cfg["DB_NAME"]],
            stdin=gz.stdout,
            check=True,
        )
        gz.wait()
    dec.unlink()
    print("Restore complete.")


def cmd_update(args: argparse.Namespace) -> None:
    """Re-deploy PHP files and static assets."""
    ensure_root()
    write_php_files()
    write_static_files()
    run_cmd(["systemctl", "reload", "nginx"])
    print("Update applied.")


def cmd_logs(args: argparse.Namespace) -> None:
    """Tail shopkit logs."""
    log = LOG_DIR / "watchdog.log"
    if log.exists():
        subprocess.run(["tail", "-n", str(getattr(args, "lines", 50)), "-f", str(log)])


def cmd_watchdog(args: argparse.Namespace) -> None:
    """Health-check watchdog: restart services if down."""
    setup_logging()
    for svc in ["nginx", "mysql"]:
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
        if r.stdout.strip() != "active":
            logger.warning("%s is not active. Restarting...", svc)
            subprocess.run(["systemctl", "restart", svc])
    php_sock = detect_php_sock()
    ver = php_sock.split("php")[1].split("-")[0] if "php" in php_sock else "8.2"
    r = subprocess.run(["systemctl", "is-active", f"php{ver}-fpm"], capture_output=True, text=True)
    if r.stdout.strip() != "active":
        logger.warning("php-fpm not active. Restarting...")
        subprocess.run(["systemctl", "restart", f"php{ver}-fpm"])
    logger.info("Watchdog check complete.")


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Remove ShopKit installation."""
    ensure_root()
    confirm = input("Type 'yes' to confirm uninstall: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return
    for path in [NGINX_CONF, NGINX_ENABLED, SYSTEMD_SERVICE, SYSTEMD_TIMER, LOGROTATE_CONF]:
        if path.exists() or path.is_symlink():
            path.unlink()
    for d in [BASE_DIR, LOG_DIR, ENV_DIR]:
        shutil.rmtree(d, ignore_errors=True)
    run_cmd(["systemctl", "daemon-reload"], check=False)
    run_cmd(["systemctl", "reload", "nginx"], check=False)
    print("ShopKit uninstalled.")


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="shopkit",
        description=f"ShopKit v{SHOPKIT_VERSION} — PHP/MySQL e-commerce deployer with BTCPay Server",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="Full installation")
    p_install.add_argument("--config", "-c", help="Path to pre-filled env config file")
    p_install.set_defaults(func=cmd_install)

    p_wizard = sub.add_parser("wizard", help="Run wizard and output env file")
    p_wizard.add_argument("--output", "-o", default="shopkit.env", help="Output file path")
    p_wizard.set_defaults(func=cmd_wizard)

    p_status = sub.add_parser("status", help="Show service status")
    p_status.set_defaults(func=cmd_status)

    p_backup = sub.add_parser("backup", help="Backup database")
    p_backup.set_defaults(func=cmd_backup)

    p_restore = sub.add_parser("restore", help="Restore from backup")
    p_restore.add_argument("file", help="Path to encrypted backup file")
    p_restore.set_defaults(func=cmd_restore)

    p_update = sub.add_parser("update", help="Re-deploy PHP files")
    p_update.set_defaults(func=cmd_update)

    p_logs = sub.add_parser("logs", help="Tail application logs")
    p_logs.add_argument("--lines", "-n", type=int, default=50)
    p_logs.set_defaults(func=cmd_logs)

    p_watchdog = sub.add_parser("watchdog", help="Health-check watchdog")
    p_watchdog.set_defaults(func=cmd_watchdog)

    p_uninstall = sub.add_parser("uninstall", help="Remove ShopKit")
    p_uninstall.set_defaults(func=cmd_uninstall)

    args = parser.parse_args()
    setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
