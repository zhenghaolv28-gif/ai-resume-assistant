param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [int]$MaxPages = 8
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq "AsTask" -and
        $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1
    } |
    Select-Object -First 1
$asTaskAction = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq "AsTask" -and
        -not $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.FullName -eq "Windows.Foundation.IAsyncAction"
    } |
    Select-Object -First 1

function Wait-WinRtOperation {
    param(
        [Parameter(Mandatory = $true)]$Operation,
        [Parameter(Mandatory = $true)][Type]$ResultType
    )

    $task = $asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

function Wait-WinRtAction {
    param([Parameter(Mandatory = $true)]$Action)

    $task = $asTaskAction.Invoke($null, @($Action))
    $task.Wait()
}

function Get-BitmapFromStream {
    param([Parameter(Mandatory = $true)]$Stream)

    $decoder = Wait-WinRtOperation `
        -Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($Stream)) `
        -ResultType ([Windows.Graphics.Imaging.BitmapDecoder])
    return Wait-WinRtOperation `
        -Operation ($decoder.GetSoftwareBitmapAsync()) `
        -ResultType ([Windows.Graphics.Imaging.SoftwareBitmap])
}

function Write-OcrLines {
    param(
        [Parameter(Mandatory = $true)]$Bitmap,
        [Parameter(Mandatory = $true)]$Engine
    )

    $result = Wait-WinRtOperation `
        -Operation ($Engine.RecognizeAsync($Bitmap)) `
        -ResultType ([Windows.Media.Ocr.OcrResult])
    foreach ($line in $result.Lines) {
        [Console]::Out.WriteLine($line.Text)
    }
}

$resolvedPath = (Resolve-Path -LiteralPath $InputPath).Path
$file = Wait-WinRtOperation `
    -Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($resolvedPath)) `
    -ResultType ([Windows.Storage.StorageFile])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    throw "Windows OCR has no installed recognition language."
}

$extension = [IO.Path]::GetExtension($resolvedPath).ToLowerInvariant()
if ($extension -eq ".pdf") {
    $pdf = Wait-WinRtOperation `
        -Operation ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) `
        -ResultType ([Windows.Data.Pdf.PdfDocument])
    if ($pdf.PageCount -gt $MaxPages) {
        throw "Scanned PDF exceeds the configured page limit."
    }

    for ($index = 0; $index -lt $pdf.PageCount; $index += 1) {
        $page = $pdf.GetPage([uint32]$index)
        $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
        $targetWidth = [Math]::Min(2600, [Math]::Max(1800, [Math]::Round($page.Dimensions.Width * 2.4)))
        $options.DestinationWidth = [uint32]$targetWidth
        Wait-WinRtAction -Action ($page.RenderToStreamAsync($stream, $options))
        $stream.Seek(0)
        $bitmap = Get-BitmapFromStream -Stream $stream
        Write-OcrLines -Bitmap $bitmap -Engine $engine
        if ($index -lt $pdf.PageCount - 1) {
            [Console]::Out.WriteLine()
        }
        $bitmap.Dispose()
        $stream.Dispose()
        $page.Dispose()
    }
} else {
    $stream = Wait-WinRtOperation `
        -Operation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) `
        -ResultType ([Windows.Storage.Streams.IRandomAccessStream])
    $bitmap = Get-BitmapFromStream -Stream $stream
    Write-OcrLines -Bitmap $bitmap -Engine $engine
    $bitmap.Dispose()
    $stream.Dispose()
}
