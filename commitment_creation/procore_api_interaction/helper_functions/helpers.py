import json
import os

def loadComsPerProj():
    with open("commitments_per_project.json", "r") as f:
        return json.load(f)
def saveComsPerProj(data):
    with open("commitments_per_project.json", "w") as f:
        json.dump(data, f, indent=4)


def getDescription(sub_name, ex_A_len, ex_B_date, ex_B_len, ex_B1_date, ex_B1_len, ex_C_len, ex_D_len, ex_H_len):
    intro = f"<p>This is a subcontract agreement between <strong>DGC Capital Contracting Corp. (\"DGC\")</strong> and <strong>{sub_name}</strong>. The terms of contract between DGC and the subcontractor are governed by the contract documents referenced herein. This subcontract supersedes all prior proposals and agreements for this project. There shall be no scope exclusions from the plans and specifications unless specifically identified in these documents. The referenced Subcontract # must appear on all correspondence (including Invoices) regarding this Subcontract. This agreement is specific to this project and includes the following attached exhibits:</p>"

    table = f"""
    <table border="1" style="border-collapse: collapse; width: 100%;">
        <tbody>
            <tr style="font-weight: bold; background-color: #f2f2f2;">
            <td>Exhibit</td>
            <td>Title</td>
            <td>Date</td>
            <td>Pages</td>
            </tr>

            <tr>
            <td>A</td>
            <td>Design Documents</td>
            <td>N/A</td>
            <td>{ex_A_len}</td>
            </tr>

            <tr>
            <td>B</td>
            <td>Subcontract Terms &amp; Conditions</td>
            <td>{ex_B_date}</td>
            <td>{ex_B_len}</td>
            </tr>

            <tr>
            <td>B1</td>
            <td>Trade Scope of Work</td>
            <td>{ex_B1_date}</td>
            <td>{ex_B1_len}</td>
            </tr>

            <tr>
            <td>C</td>
            <td>Insurance Requirements</td>
            <td>N/A</td>
            <td>{ex_C_len}</td>
            </tr>

            <tr>
            <td>D</td>
            <td>Monthly Billing Procedures</td>
            <td>N/A</td>
            <td>{ex_D_len}</td>
            </tr>

            <tr>
            <td>E</td>
            <td>Additional Documents</td>
            <td>N/A</td>
            <td>{ex_H_len}</td>
            </tr>
        </tbody>
    </table>

    """

    return intro + table
