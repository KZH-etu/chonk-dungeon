# Starts the Chonk build of Shattered Pixel Dungeon (the fork that exports chonk_state.json).
# Paths can be changed with the CHONK_JAVA and CHONK_JAR environment variables.
$java = if ($env:CHONK_JAVA) { $env:CHONK_JAVA } else { "$env:USERPROFILE\tools\jdk-21.0.12.1+1\bin\javaw.exe" }
$jar = if ($env:CHONK_JAR) { $env:CHONK_JAR } else { "$PSScriptRoot\..\..\spd-chonk\desktop\build\libs\desktop-4.0.2.jar" }
if (-not (Test-Path $java)) { Write-Error "Java not found: $java (set CHONK_JAVA)"; exit 1 }
if (-not (Test-Path $jar)) { Write-Error "Game jar not found: $jar (build the fork, or set CHONK_JAR)"; exit 1 }
Start-Process -FilePath $java -ArgumentList "-jar", "`"$jar`"" -WorkingDirectory (Split-Path $jar)
