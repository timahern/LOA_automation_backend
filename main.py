from loaListGenerator import LoaGenerator  
import glob
import os
import b1Extractor

pdfPath = "C:\\Users\\tahern\\Desktop\\LOA-Automation\\Ignite Plumbing Buyout.PDF"
outputPath = "C:\\Users\\tahern\\Desktop\\LOA-Automation\\Ignite Plumbing B1.PDF"

b1Extractor.extract_b1_with_ocr(pdfPath, outputPath)

