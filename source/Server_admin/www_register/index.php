// [Zeitmessung] Project Version
$PROJECT_VERSION = '0.1.0';
echo "<div style='color: #888; font-size: small;'>[Zeitmessung] Project Version: $PROJECT_VERSION</div>\n";
<?php
	if (!empty($_SERVER['HTTPS']) && ('on' == $_SERVER['HTTPS'])) {
		$uri = 'https://';
	} else {
		$uri = 'http://';
	}
	$uri .= $_SERVER['HTTP_HOST'];
	header('Location: '.$uri.'/register.php');
	exit;
?>
Something is wrong with the XAMPP (server) installation :-(
