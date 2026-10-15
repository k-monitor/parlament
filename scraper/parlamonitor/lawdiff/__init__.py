"""What a bill changes (BILL-14..16): the law diff against njt.hu, the bill's own
evolution through its egységes javaslat, and each amendment as a diff.

* :mod:`.njt` — njt.hu time states, read as addressable units
* :mod:`.pdftext` — iromány PDFs read with layout and insert/delete markup
* :mod:`.novella` — amending instructions parsed and applied to a law
* :mod:`.motions` — a módosító javaslat's points as diffs of the bill
* :mod:`.scrape` — the stage that ties them together per bill
"""
