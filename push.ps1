# push.ps1
$env:HTTPS_PROXY="http://127.0.0.1:15236"
git add .
git commit -m $args[0]
git push origin main