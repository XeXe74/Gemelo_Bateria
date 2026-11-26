# Abre un .docx con Word, actualiza el índice y los campos, lo guarda y lo exporta a PDF.
# Uso: powershell -File herramientas/word_pdf.ps1 -Docx "documentos/CONOPS Gemelo digital de la batería Mario Tuset.docx"
param([Parameter(Mandatory = $true)][string]$Docx)

$ruta = (Resolve-Path $Docx).Path
$pdf = [System.IO.Path]::ChangeExtension($ruta, ".pdf")
$word = New-Object -ComObject Word.Application
$word.Visible = $false
try {
    $doc = $word.Documents.Open($ruta)
    foreach ($toc in $doc.TablesOfContents) { $toc.Update() }
    $doc.Fields.Update() | Out-Null
    $doc.Save()
    $doc.ExportAsFixedFormat($pdf, 17)  # 17 = PDF
    $doc.Close()
    Write-Output "PDF: $pdf"
}
finally {
    $word.Quit()
}
