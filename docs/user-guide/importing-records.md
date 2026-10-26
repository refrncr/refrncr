# Importing Records

An import describes the complete set of Records you want in a Dataset; it does not simply append rows. Refrncr compares the file with the Draft's current Records and marks each one as Added, Changed, Unchanged or Deleted. Review the preview and confirm the deletion count before importing.

For a Dataset that has never been published (no Scheduled, Current or Expired version), you can import after adding, editing or deleting Records by hand, and you can import again after an earlier import. Each preview compares the file with the Records currently in the Draft. The **Import results** filter on the Records tab shows the results of the latest import only.

After a Dataset has a version, imports into a Draft are restricted to protect its existing content: once Records in that Draft have changed or an import has completed, another import is unavailable. Publish or discard the Draft before starting a new import. Older Drafts whose Record history cannot be verified also remain unavailable for import.

Map every required Field and every identifier Field. Optional Fields can be left unmapped:
existing Records keep their values from the Draft, and new Records leave those Fields empty.
The preview lists them under **Not in file**. A mapped empty cell deliberately clears an optional
value; an empty required value prevents the import. Changes are counted only when mapped values
differ, so retaining a Field does not mark a Record Changed.

The file must still contain the complete desired set of Records. Records absent from the file
become Deleted after you acknowledge the deletion count. If an identifier belongs only to a
previously Deleted Record, importing it creates an Added Record with empty unmapped Fields;
it does not recover the Deleted Record's values. Use **Undo delete** to restore a Deleted Record.
