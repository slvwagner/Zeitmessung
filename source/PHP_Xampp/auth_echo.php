<?php
header('Content-Type: application/json; charset=UTF-8');
$hdrs = function_exists('getallheaders') ? getallheaders() : [];
$raw  = file_get_contents('php://input');
echo json_encode([
    'ok' => true,
    'server' => [
        'HTTP_X_API_KEY' => $_SERVER['HTTP_X_API_KEY'] ?? null,
        'CONTENT_TYPE'   => $_SERVER['CONTENT_TYPE'] ?? null,
        'REQUEST_METHOD' => $_SERVER['REQUEST_METHOD'] ?? null,
    ],
    'headers' => $hdrs,
    'raw_body' => $raw,
], JSON_UNESCAPED_UNICODE);
